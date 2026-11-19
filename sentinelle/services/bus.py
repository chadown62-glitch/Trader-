"""Bus d'événements : diffuse en temps réel ce qui se passe (prix, ordres,
positions, décisions de l'IA, alertes) vers le panel (WebSocket) et Telegram.

`publish` peut être appelé depuis n'importe quel thread.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import json
import uuid
from typing import Any, Callable

log = logging.getLogger("sentinelle.bus")

Listener = Callable[[str, dict], None]


class EventBus:
    DURABLE = frozenset({'position_opened', 'position_updated', 'position_closed', 'trade_closed', 'decision'})

    def __init__(self, db=None) -> None:
        self.db = db
        self._dispatch_lock = threading.RLock()
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
        if self.db is not None and topic in self.DURABLE:
            event['event_id'] = uuid.uuid4().hex
            with self.db.transaction():
                self.db.insert('event_outbox', {'id':event['event_id'], 'topic':topic,
                    'event':json.dumps(event,ensure_ascii=False,allow_nan=False), 'created_at':event['ts']})
                self.db.after_commit(self.drain)
            return
        self._emit(topic, event)

    def drain(self, limit=100):
        """At-least-once dispatch; consumers must deduplicate stable event_id.

        dispatched_at acknowledges local listener acceptance, not remote delivery.
        Never use this marker as proof that Telegram received a notification.
        """
        if self.db is None:
            return 0
        with self._lock:
            if not self._listeners and not self._queues:
                return 0  # no recipient: retain the event for an explicitly attached consumer
        count = 0
        with self._dispatch_lock:
            for row in self.db.query('SELECT * FROM event_outbox WHERE dispatched_at IS NULL ORDER BY created_at,id LIMIT ?',
                                     (max(1,min(1000,limit)),)):
                event = json.loads(row['event'])
                ok = self._emit(row['topic'], event)
                self.db.execute('UPDATE event_outbox SET attempts=attempts+1,dispatched_at=?,error=? WHERE id=?',
                                (time.time() if ok else None, None if ok else 'listener failed', row['id']))
                count += int(ok)
        return count

    def _emit(self, topic, event):
        ok = True
        with self._lock:
            listeners = list(self._listeners)
            queues = list(self._queues)
        for fn in listeners:
            try:
                fn(topic, event)
            except Exception:  # un abonné défaillant ne doit jamais bloquer le bot
                ok = False
                log.exception("Erreur dans un abonné du bus (%s)", topic)
        if not queues:
            return ok
        loop = self._loop
        if loop is None or loop.is_closed():
            return ok
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
                ok = False
        return ok

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
