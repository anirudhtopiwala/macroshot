"""Tests for web/src/utils/requestScheduler.ts logic.

Since the scheduler is a TypeScript module, we test the equivalent logic
pattern here in Python to validate the design (signal/wait/timeout).
"""

import asyncio

import pytest


# Replicate the scheduler logic in Python for testing
class RequestScheduler:
    def __init__(self):
        self._done = False
        self._event = asyncio.Event()

    def signal_critical_done(self):
        if self._done:
            return
        self._done = True
        self._event.set()

    async def wait_for_critical(self, timeout: float = 3.0):
        if self._done:
            return
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass


@pytest.mark.asyncio
async def test_wait_resolves_on_signal():
    """waitForCritical should resolve when signalCriticalDone is called."""
    s = RequestScheduler()

    async def signal_after_delay():
        await asyncio.sleep(0.05)
        s.signal_critical_done()

    asyncio.create_task(signal_after_delay())
    await s.wait_for_critical(timeout=2.0)
    assert s._done


@pytest.mark.asyncio
async def test_wait_resolves_on_timeout():
    """waitForCritical should resolve after timeout if signal never fires."""
    s = RequestScheduler()
    await s.wait_for_critical(timeout=0.1)
    # Should not hang - timeout resolves it
    assert not s._done  # Signal was never called


@pytest.mark.asyncio
async def test_signal_idempotent():
    """Calling signalCriticalDone multiple times should be safe."""
    s = RequestScheduler()
    s.signal_critical_done()
    s.signal_critical_done()
    s.signal_critical_done()
    assert s._done


@pytest.mark.asyncio
async def test_wait_after_signal_resolves_immediately():
    """If signal already fired, waitForCritical returns immediately."""
    s = RequestScheduler()
    s.signal_critical_done()

    import time
    start = time.monotonic()
    await s.wait_for_critical(timeout=5.0)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1  # Should be near-instant


@pytest.mark.asyncio
async def test_multiple_waiters():
    """Multiple concurrent waiters should all resolve when signal fires."""
    s = RequestScheduler()
    results = []

    async def waiter(name: str):
        await s.wait_for_critical(timeout=2.0)
        results.append(name)

    tasks = [asyncio.create_task(waiter(f"w{i}")) for i in range(5)]

    await asyncio.sleep(0.05)
    s.signal_critical_done()
    await asyncio.gather(*tasks)

    assert len(results) == 5
