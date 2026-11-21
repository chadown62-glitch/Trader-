"""One opt-in asyncio task per local API process. A pause drains the current tick.

A failed cycle never stops the worker for good: the error is recorded and the next cycle waits a bounded, growing delay
(at most ``MAX_BACKOFF_S``). A security refusal is the exception: it stops the worker, which then needs a deliberate
restart. Each cycle commits or rolls back on its own (``memecoin_simulator.tick``), so no partial state is left behind.
"""

import asyncio
import time

from ..safety import RealTradingForbidden
from .ledger import Blocked
from .security import SecurityRefusal

MAX_BACKOFF_S = 900  # 15 min: the longest wait after repeated failures, then a cycle is tried again
ERROR_LOG = 20  # last cycle failures kept in the status
FATAL = (RealTradingForbidden, SecurityRefusal, Blocked)  # never retried: a refusal is an answer, not an outage


class CycleBusy(Exception):
    """A manual scan or the shared rate limit already owns this interval."""


def backoff(interval, failures):
    """Delay before the next cycle: the interval while cycles succeed, then doubled per consecutive failure, bounded by
    ``MAX_BACKOFF_S`` (or by the interval itself when it is longer: a failure never shortens the cadence)."""
    if failures <= 0:
        return interval
    return min(max(interval, MAX_BACKOFF_S), interval * 2 ** min(failures, 16))


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
        self.failures = 0  # consecutive failed cycles
        self.failed_cycles = 0
        self.errors = []
        self.next_delay = None

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
            "consecutive_failures": self.failures,
            "failed_cycles": self.failed_cycles,
            "recent_errors": self.errors[-5:],
            "next_delay_seconds": self.next_delay,
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
            self.failures = 0
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

    def _failed(self, exc):
        self.failures += 1
        self.failed_cycles += 1
        self.error = str(exc)[:240] or type(exc).__name__
        self.errors = (self.errors + [{"ts": time.time(), "type": type(exc).__name__, "error": self.error}])[-ERROR_LOG:]

    async def _run(self):
        try:
            while not self._stop.is_set():
                try:
                    await asyncio.to_thread(self.cycle)
                    self.cycles += 1
                    self.last_cycle = time.time()
                    self.failures = 0
                    self.error = None
                except CycleBusy:
                    pass
                except FATAL:
                    raise
                except Exception as exc:  # noqa: BLE001 - an outage or a transient database error: retried later
                    self._failed(exc)
                self.next_delay = backoff(self.interval, self.failures)
                self._publish()
                if not self._stop.is_set():
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=self.next_delay)
                    except TimeoutError:
                        pass
        except Exception as exc:  # a security refusal (or a defect of the loop itself) stops the worker
            self._failed(exc)
        finally:
            self._task = None
            self.next_delay = None
            self._publish()
