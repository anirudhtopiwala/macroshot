"""Tests for the Gemini monthly-spend budget gate."""

import pytest
import pytest_asyncio

from src.db import get_monthly_gemini_cost_usd, init_db, log_gemini_call
from src.web import budget_gate
from src.web.budget_gate import BudgetExceededError, assert_gemini_budget


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    await init_db(path)
    # Reset module-level cache between tests so one test's spend
    # doesn't leak into the next via budget_gate's 60s cache.
    budget_gate._reset_cache_for_tests()
    return path


# ---------------------------------------------------------------------------
# get_monthly_gemini_cost_usd
# ---------------------------------------------------------------------------

async def test_monthly_cost_empty_db_is_zero(db):
    cost = await get_monthly_gemini_cost_usd(db)
    assert cost == 0.0


async def test_monthly_cost_sums_tokens_at_flash_lite_rates(db):
    # 1M input + 1M output = $0.10 + $0.40 = $0.50
    await log_gemini_call(db, call_type="analyze_meal", user_id=1,
                          input_tokens=1_000_000, output_tokens=1_000_000)
    cost = await get_monthly_gemini_cost_usd(db)
    assert cost == pytest.approx(0.50, rel=1e-6)


async def test_monthly_cost_accumulates_across_calls(db):
    for _ in range(3):
        await log_gemini_call(db, call_type="analyze_meal", user_id=1,
                              input_tokens=500_000, output_tokens=250_000)
    # 3 × (500k × $0.10/M + 250k × $0.40/M) = 3 × ($0.05 + $0.10) = $0.45
    cost = await get_monthly_gemini_cost_usd(db)
    assert cost == pytest.approx(0.45, rel=1e-6)


# ---------------------------------------------------------------------------
# assert_gemini_budget
# ---------------------------------------------------------------------------

async def test_gate_allows_calls_under_cap(db, monkeypatch):
    monkeypatch.setattr(budget_gate, "MONTHLY_GEMINI_BUDGET_USD", 40.0)
    # Log a tiny call - way under $40
    await log_gemini_call(db, call_type="analyze_meal", user_id=1,
                          input_tokens=1000, output_tokens=1000)
    # Should not raise
    await assert_gemini_budget(db)


async def test_gate_blocks_calls_at_or_over_cap(db, monkeypatch):
    monkeypatch.setattr(budget_gate, "MONTHLY_GEMINI_BUDGET_USD", 0.50)
    # 1M input + 1M output = $0.50 - exactly at cap, must trip
    await log_gemini_call(db, call_type="analyze_meal", user_id=1,
                          input_tokens=1_000_000, output_tokens=1_000_000)
    with pytest.raises(BudgetExceededError):
        await assert_gemini_budget(db)


async def test_gate_fails_open_on_db_error(monkeypatch):
    """If the DB query throws, the gate must not block traffic - the
    Pub/Sub kill switch is the real backstop for catastrophic spend."""
    async def _broken(_path):
        raise RuntimeError("simulated SQLite failure")

    monkeypatch.setattr(budget_gate, "get_monthly_gemini_cost_usd", _broken)
    budget_gate._reset_cache_for_tests()
    # Should not raise
    await assert_gemini_budget("/nonexistent")


async def test_gate_caches_reads_within_ttl(db, monkeypatch):
    """Within the 60s TTL, repeat calls should hit the cache, not the DB."""
    monkeypatch.setattr(budget_gate, "MONTHLY_GEMINI_BUDGET_USD", 40.0)

    call_count = {"n": 0}
    real_fn = budget_gate.get_monthly_gemini_cost_usd

    async def _counted(path):
        call_count["n"] += 1
        return await real_fn(path)

    monkeypatch.setattr(budget_gate, "get_monthly_gemini_cost_usd", _counted)

    await assert_gemini_budget(db)
    await assert_gemini_budget(db)
    await assert_gemini_budget(db)

    assert call_count["n"] == 1
