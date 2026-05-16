"""Per-case model invocation.

For each (case, model, config) we call the meal-analysis pipeline,
optionally enriched with the FatSecret reference step that production
uses, and record items, latency, tokens, and cost.

Results are cached on disk keyed by (case_id, model, config) so re-runs
after editing ground-truth are free for unchanged inputs.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .dataset import Case, EVAL_ROOT, load_image_bytes


# Project root on sys.path so we can import src.* without packaging.
_PROJ = Path(__file__).resolve().parents[2]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))


# ── Pricing ────────────────────────────────────────────────────────────────
# Per-million-token pricing in USD (input, output). Update from
# https://ai.google.dev/pricing whenever Google changes rates. The eval
# raises KeyError if a candidate model isn't priced - that's intentional,
# so a price oversight can't silently turn into a meaningless score.
PRICE_TABLE: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash":      (0.30, 2.50),
    "gemini-2.5-pro":        (1.25, 10.00),
    "gemini-2.0-flash":      (0.10, 0.40),
    "gemini-2.0-flash-lite": (0.075, 0.30),
}


# ── Token-capture plumbing ────────────────────────────────────────────────
# gemini_analyze_meal logs token counts to the gemini_calls table. We
# capture them in-process via a contextvar instead of writing to a temp
# DB - fewer moving parts. Set up once at module import so we don't have
# to monkey-patch repeatedly.
_token_capture: contextvars.ContextVar[dict[str, int] | None] = contextvars.ContextVar(
    "_eval_token_capture", default=None
)


def _install_token_capture() -> None:
    import src.db as _dbmod

    if getattr(_dbmod, "_eval_patched", False):
        return
    original = _dbmod.log_gemini_call

    async def _wrapped(*args, **kwargs):
        slot = _token_capture.get()
        if slot is not None:
            # Accumulate across every Gemini call in the current case - the
            # with_fatsecret config invokes gemini_chat a second time for the
            # reference-hint turn; earlier code used set-latest and dropped
            # the first call's tokens, making with_fatsecret look cheaper
            # than it really is.
            slot["input_tokens"] = slot.get("input_tokens", 0) + int(kwargs.get("input_tokens", 0) or 0)
            slot["output_tokens"] = slot.get("output_tokens", 0) + int(kwargs.get("output_tokens", 0) or 0)
        # Don't actually write to the DB - eval calls shouldn't pollute prod stats.
        return None

    _dbmod.log_gemini_call = _wrapped
    _dbmod._eval_patched = True


# ── Result type ───────────────────────────────────────────────────────────
@dataclass
class CallResult:
    case_id: str
    model: str
    config: str  # "raw" | "with_fatsecret"
    items: list[dict[str, Any]] = field(default_factory=list)
    totals: dict[str, float] = field(default_factory=dict)
    raw_response: str = ""
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    failed: bool = False
    error: str | None = None
    fatsecret_used: bool = False


def _cost(model: str, in_tok: int, out_tok: int) -> float:
    if model not in PRICE_TABLE:
        raise KeyError(
            f"No price entry for model {model!r}. Add it to PRICE_TABLE in "
            f"scripts/eval_lib/runners.py before benchmarking."
        )
    in_per_m, out_per_m = PRICE_TABLE[model]
    return (in_tok / 1_000_000) * in_per_m + (out_tok / 1_000_000) * out_per_m


# ── Caching ───────────────────────────────────────────────────────────────
CACHE_DIR = EVAL_ROOT / "cache"


def _cache_key(case: Case, model: str, config: str) -> str:
    # Include the verifier so recomputing after a ground-truth fix doesn't
    # invalidate cached model responses (responses don't depend on truth).
    # Include input bytes so changing the source image busts cache.
    h = hashlib.sha256()
    h.update(case.id.encode())
    h.update(b"|")
    h.update(model.encode())
    h.update(b"|")
    h.update(config.encode())
    h.update(b"|")
    h.update((case.text or "").encode())
    if case.image_path:
        img_path = case.absolute_image_path
        if img_path and img_path.exists():
            h.update(b"|")
            # Hash the full file - two photos with identical JPEG headers
            # (same camera/settings) collided under the old first-64KB
            # fingerprint. SHA-256 on a few MB is sub-millisecond.
            h.update(img_path.read_bytes())
    return h.hexdigest()[:16]


def _cache_path(case: Case, model: str, config: str) -> Path:
    return CACHE_DIR / f"{_cache_key(case, model, config)}.json"


def _cached(case: Case, model: str, config: str) -> CallResult | None:
    p = _cache_path(case, model, config)
    if not p.exists():
        return None
    try:
        with p.open() as f:
            data = json.load(f)
        return CallResult(**data)
    except Exception:
        return None


def _write_cache(result: CallResult) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = _cache_path_from_result(result)
    if p is None:
        return
    with p.open("w") as f:
        json.dump(asdict(result), f, indent=2)


def _cache_path_from_result(result: CallResult) -> Path | None:
    # We need a Case to recompute the key, but since we only call this from
    # `run_case` which has the Case in scope, we pass the path explicitly there.
    return None  # caller writes via _cache_path directly


# ── Main runner ───────────────────────────────────────────────────────────
async def run_case(case: Case, model: str, config: str, *, use_cache: bool = True) -> CallResult:
    """Run one (case, model, config) triple. Returns a populated CallResult."""
    if config not in {"raw", "with_fatsecret"}:
        raise ValueError(f"Unknown config: {config}")

    if use_cache:
        hit = _cached(case, model, config)
        if hit is not None:
            return hit

    _install_token_capture()
    from src.gemini import _meal_model_override, gemini_analyze_meal
    from src.services import apply_references

    images: list[bytes] = []
    if case.input_mode in {"image", "image_text"}:
        b = load_image_bytes(case)
        if b:
            images.append(b)
    text = case.text or "" if case.input_mode in {"text", "image_text"} else ""

    result = CallResult(case_id=case.id, model=model, config=config)
    token_slot: dict[str, int] = {}
    token = _token_capture.set(token_slot)
    # Use a ContextVar instead of os.environ so concurrent calls don't race.
    model_token = _meal_model_override.set(model)

    try:
        t0 = time.perf_counter()
        try:
            raw_text, nutrition = await gemini_analyze_meal(
                images=images, user_text=text,
                # Pass a non-None db_path so token logging fires;
                # _wrapped above intercepts and doesn't actually write.
                db_path="eval-noop", user_id=0,
            )
        except Exception as e:
            result.failed = True
            result.error = f"{type(e).__name__}: {e}"
            result.latency_ms = (time.perf_counter() - t0) * 1000
            return result

        if nutrition is None:
            result.failed = True
            result.error = "JSON parse failed or empty response"
            result.raw_response = raw_text
            result.latency_ms = (time.perf_counter() - t0) * 1000
            return result

        if config == "with_fatsecret":
            try:
                from src.gemini import (
                    COMBINED_INITIAL_PROMPT,
                    CONVERSATIONAL_INITIAL_PROMPT,
                    TEXT_ONLY_INITIAL_PROMPT,
                )
                if images and text:
                    initial = COMBINED_INITIAL_PROMPT + f"\n\nUser's note: {text}"
                elif not images:
                    initial = TEXT_ONLY_INITIAL_PROMPT + f"\n\nUser's description: {text}"
                else:
                    initial = CONVERSATIONAL_INITIAL_PROMPT
                conversation = [
                    {"role": "user", "text": initial},
                    {"role": "model", "text": raw_text or ""},
                ]
                nutrition, fs_used, _mem = await apply_references(
                    images, conversation, nutrition, db_path=None, user_id=0,
                )
                result.fatsecret_used = bool(fs_used)
            except Exception as e:
                # Don't fail the whole case if FatSecret enrichment errors -
                # mark it so the report shows what happened, keep the raw result.
                result.error = f"with_fatsecret enrichment failed: {type(e).__name__}: {e}"

        result.latency_ms = (time.perf_counter() - t0) * 1000
        result.raw_response = raw_text
        result.input_tokens = token_slot.get("input_tokens", 0)
        result.output_tokens = token_slot.get("output_tokens", 0)
        result.cost_usd = _cost(model, result.input_tokens, result.output_tokens)
        result.items = [
            {
                "name": it.name,
                "description": getattr(it, "description", "") or "",
                "weight_g": getattr(it, "weight_g", None),
                "calories": float(getattr(it, "calories", 0) or 0),
                "protein": float(getattr(it, "protein", 0) or 0),
                "carbs": float(getattr(it, "carbs", 0) or 0),
                "fat": float(getattr(it, "fat", 0) or 0),
            }
            for it in nutrition.items
        ]
        result.totals = {
            "calories": float(sum(i["calories"] for i in result.items)),
            "protein": float(sum(i["protein"] for i in result.items)),
            "carbs": float(sum(i["carbs"] for i in result.items)),
            "fat": float(sum(i["fat"] for i in result.items)),
        }
    finally:
        _token_capture.reset(token)
        _meal_model_override.reset(model_token)

    # Cache successful runs only - failures might be transient (rate limit etc).
    if not result.failed:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with _cache_path(case, model, config).open("w") as f:
            json.dump(asdict(result), f, indent=2)

    return result


async def run_all(
    cases: list[Case],
    models: list[str],
    configs: list[str],
    *,
    use_cache: bool = True,
    concurrency: int = 4,
) -> list[CallResult]:
    """Run every (case, model, config) triple. Returns flat list of results."""
    sem = asyncio.Semaphore(concurrency)
    results: list[CallResult] = []

    async def _one(c: Case, m: str, cfg: str) -> None:
        async with sem:
            r = await run_case(c, m, cfg, use_cache=use_cache)
            results.append(r)

    triples = [(c, m, cfg) for c in cases for m in models for cfg in configs]
    await asyncio.gather(*(_one(*t) for t in triples))
    return results
