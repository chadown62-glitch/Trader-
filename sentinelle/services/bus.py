"""Bus d'événements : diffuse en temps réel ce qui se passe (prix, ordres,
positions, décisions de l'IA, alertes) vers le panel (WebSocket) et Telegram.

`publish` peut être appelé depuis n'importe quel thread.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable

log = logging.getLogger("sentinelle.bus")

Listener = Callable[[str, dict], None]


class EventBus:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: set[asyncio.Queue] = set()
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # -- abonnés asynchrones (WebSocket) -----------------------------------
    def subscribe(self, maxsize: int = 2000) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        with self._lock:
            self._queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._queues.discard(q)

    # -- abonnés synchrones (Telegram, tests) -------------------------------
    def add_listener(self, fn: Listener) -> None:
        with self._lock:
            self._listeners.append(fn)

    def remove_listener(self, fn: Listener) -> None:
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    # -- publication --------------------------------------------------------
    def publish(self, topic: str, payload: dict[str, Any] | None = None) -> None:
        event = {"topic": topic, "ts": time.time(), "data": payload or {}}
        with self._lock:
            listeners = list(self._listeners)
            queues = list(self._queues)
        for fn in listeners:
            try:
                fn(topic, event)
            except Exception:  # un abonné défaillant ne doit jamais bloquer le bot
                log.exception("Erreur dans un abonné du bus (%s)", topic)
        if not queues:
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._deliver(queues, event)
        else:
            try:
                loop.call_soon_threadsafe(self._deliver, queues, event)
            except RuntimeError:  # boucle arrêtée
                pass

    @staticmethod
    def _deliver(queues: list[asyncio.Queue], event: dict) -> None:
        for q in queues:
            if q.full():
                try:
                    q.get_nowait()  # on jette le plus ancien plutôt que de bloquer
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
