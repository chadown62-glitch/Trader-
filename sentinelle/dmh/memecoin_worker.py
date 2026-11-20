"""One opt-in asyncio task per local API process. A pause drains the current tick."""

import asyncio
import time


class CycleBusy(Exception):
    """A manual scan or the shared rate limit already owns this interval."""


class MemecoinSessionWorker:
    def __init__(self, cycle, configure, bus):
        self.cycle = cycle
        self.configure = configure
        self.bus = bus
        self._task = None
        self._stop = asyncio.Event()
        self._control = asyncio.Lock()
        self.interval = 15
        self.cycles = 0
        self.last_cycle = None
        self.error = None

    def status(self):
        active = self._task is not None and not self._task.done()
        return {
            "asset_domain": "memecoin",
            "active": active,
            "stopping": active and self._stop.is_set(),
            "interval_seconds": self.interval,
            "cycles": self.cycles,
            "last_cycle": self.last_cycle,
            "error": self.error,
        }

    def _publish(self):
        self.bus.publish("memecoin_worker", self.status())

    async def start(self, interval, capital=None, currency=None):
        if type(interval) is not int or not 15 <= interval <= 3600:
            raise ValueError("Intervalle entier requis entre 15 et 3 600 secondes")
        async with self._control:
            await asyncio.to_thread(self.configure, capital, currency)
            if self.status()["active"]:
                if interval != self.interval:
                    raise ValueError("Mettre en pause avant de changer l’intervalle")
                return self.status()
            self.interval = interval
            self.error = None
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="memecoin-paper")
            self._publish()
            return self.status()

    async def stop(self):
        async with self._control:
            if self._task is not None:
                self._stop.set()
                self._publish()
                await asyncio.shield(self._task)
            return self.status()

    async def _run(self):
        try:
            while not self._stop.is_set():
                try:
                    await asyncio.to_thread(self.cycle)
                    self.cycles += 1
                    self.last_cycle = time.time()
                    self._publish()
                except CycleBusy:
                    pass
                if not self._stop.is_set():
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
                    except TimeoutError:
                        pass
        except Exception as exc:
            self.error = str(exc)[:240] or type(exc).__name__
        finally:
            self._task = None
            self._publish()
