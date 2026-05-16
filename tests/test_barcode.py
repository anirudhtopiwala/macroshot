"""Tests for barcode lookup nutriment parsing, sanity checks, and user corrections."""

import pytest

from src.barcode_lookup import _parse_nutriments
from src.db import (
    delete_barcode_correction,
    ensure_user,
    get_barcode_correction,
    init_db,
    save_barcode_correction,
)
from src.models import FoodItem, NutritionResult
from src.services import _macros_differ, _maybe_save_barcode_correction


class TestParseNutriments:
    def test_normal_per_serving(self):
        """Normal product with correct per-serving data."""
        result = _parse_nutriments(
            {"energy-kcal_100g": 50, "proteins_100g": 5, "carbohydrates_100g": 6, "fat_100g": 2,
             "energy-kcal_serving": 150, "proteins_serving": 15, "carbohydrates_serving": 18, "fat_serving": 6},
            serving_g=300.0,
        )
        assert result["calories"] == 150.0
        assert result["protein"] == 15.0

    def test_per_100g_only(self):
        """No per-serving data, computed from per-100g."""
        result = _parse_nutriments(
            {"energy-kcal_100g": 200, "proteins_100g": 10, "carbohydrates_100g": 25, "fat_100g": 8},
            serving_g=50.0,
        )
        assert result["calories"] == 100.0
        assert result["protein"] == 5.0

    def test_sanity_fix_inflated_serving(self):
        """Fairlife-style bug: per_100g has per-bottle values, per_serving is 3.4x inflated."""
        result = _parse_nutriments(
            {"energy-kcal_100g": 150, "proteins_100g": 30, "carbohydrates_100g": 4, "fat_100g": 2.5,
             "energy-kcal_serving": 510, "proteins_serving": 102, "carbohydrates_serving": 13.6, "fat_serving": 8.5},
            serving_g=340.0,
        )
        # Should use per_100g as per-serving (sanity fix)
        assert result["calories"] == 150.0
        assert result["protein"] == 30.0
        assert result["carbs"] == 4.0
        assert result["fat"] == 2.5

    def test_sanity_fix_computed_from_100g(self):
        """No per_serving from OFF, but computed values are unreasonable."""
        result = _parse_nutriments(
            {"energy-kcal_100g": 150, "proteins_100g": 30, "carbohydrates_100g": 4, "fat_100g": 2.5},
            serving_g=340.0,
        )
        # Computed: 510 cal, 102g protein - unreasonable, should fall back to per_100g
        assert result["calories"] == 150.0
        assert result["protein"] == 30.0

    def test_no_sanity_fix_small_serving(self):
        """Small serving size (<150g) - don't apply sanity fix even if values seem high."""
        result = _parse_nutriments(
            {"energy-kcal_100g": 500, "proteins_100g": 40, "carbohydrates_100g": 30, "fat_100g": 20,
             "energy-kcal_serving": 500, "proteins_serving": 40, "carbohydrates_serving": 30, "fat_serving": 20},
            serving_g=100.0,
        )
        # 100g serving - don't mess with it
        assert result["calories"] == 500.0
        assert result["protein"] == 40.0

    def test_no_sanity_fix_reasonable_large_serving(self):
        """Large serving but reasonable values - don't apply sanity fix."""
        result = _parse_nutriments(
            {"energy-kcal_100g": 100, "proteins_100g": 5, "carbohydrates_100g": 15, "fat_100g": 3,
             "energy-kcal_serving": 350, "proteins_serving": 17.5, "carbohydrates_serving": 52.5, "fat_serving": 10.5},
            serving_g=350.0,
        )
        # 350 cal, 17.5g protein - reasonable, keep as-is
        assert result["calories"] == 350.0
        assert result["protein"] == 17.5

    def test_atwater_fix_inflated_kcal_small_serving(self):
        """Peanut butter case: OFF returns energy-kcal_100g=19000 (contributor
        error), energy-kcal_serving=6080, but macros are correct. 32g serving
        is below the >150g heuristic, so only Atwater catches it."""
        result = _parse_nutriments(
            {"energy-kcal_100g": 19000, "proteins_100g": 21.88, "carbohydrates_100g": 21.88, "fat_100g": 56.25,
             "energy-kcal_serving": 6080, "proteins_serving": 7, "carbohydrates_serving": 7, "fat_serving": 18},
            serving_g=32.0,
        )
        # Atwater-implied: 7*4 + 7*4 + 18*9 = 218 kcal per serving
        assert result["calories"] == 218.0
        assert result["protein"] == 7.0
        assert result["carbs"] == 7.0
        assert result["fat"] == 18.0
        # cal_per_100g should be recomputed from corrected per-serving: 218 * 100/32 ≈ 681
        assert 670 <= result["cal_per_100g"] <= 695

    def test_atwater_fix_deflated_kcal(self):
        """Ranch dressing case: OFF returns energy=2 kcal, fat=6g - physically
        impossible (fat alone is 54 kcal). Atwater should recompute."""
        result = _parse_nutriments(
            {"energy-kcal_100g": 6.67, "proteins_100g": 0, "carbohydrates_100g": 6.67, "fat_100g": 20,
             "energy-kcal_serving": 2, "proteins_serving": 0, "carbohydrates_serving": 2, "fat_serving": 6},
            serving_g=30.0,
        )
        # Atwater-implied: 0*4 + 2*4 + 6*9 = 62 kcal per serving
        assert result["calories"] == 62.0
        assert result["protein"] == 0.0
        assert result["fat"] == 6.0

    def test_atwater_no_fix_near_zero_macros(self):
        """Products with near-zero macros (e.g. black coffee, sparkling water)
        shouldn't trigger Atwater - implied_kcal threshold guards this."""
        result = _parse_nutriments(
            {"energy-kcal_100g": 2, "proteins_100g": 0.1, "carbohydrates_100g": 0.2, "fat_100g": 0,
             "energy-kcal_serving": 5, "proteins_serving": 0.2, "carbohydrates_serving": 0.5, "fat_serving": 0},
            serving_g=250.0,
        )
        # implied_kcal = 0.2*4 + 0.5*4 + 0 = 2.8 kcal - below the >10 threshold,
        # so we keep stated 5 even though ratio looks bad
        assert result["calories"] == 5.0
        assert result["protein"] == 0.2

    def test_atwater_no_fix_within_tolerance(self):
        """Products within ±30% Atwater tolerance keep their stated calories
        (fiber, sugar alcohols, etc. can cause small discrepancies)."""
        result = _parse_nutriments(
            {"energy-kcal_100g": 50, "proteins_100g": 5, "carbohydrates_100g": 6, "fat_100g": 2,
             "energy-kcal_serving": 100, "proteins_serving": 10, "carbohydrates_serving": 12, "fat_serving": 4},
            serving_g=200.0,
        )
        # Atwater: 40 + 48 + 36 = 124. Stated 100. Ratio = 24/124 = 19.4%. Within tolerance.
        assert result["calories"] == 100.0


# ── _macros_differ helper (correction threshold) ──────────────────

class TestMacrosDiffer:
    def test_identical_macros_not_different(self):
        assert not _macros_differ(
            {"calories": 190, "protein": 7, "carbs": 7, "fat": 18},
            {"calories": 190, "protein": 7, "carbs": 7, "fat": 18},
            0.05,
        )

    def test_rounding_noise_not_different(self):
        # 190 vs 192 is ~1% - below 5% threshold
        assert not _macros_differ(
            {"calories": 190, "protein": 7, "carbs": 7, "fat": 18},
            {"calories": 192, "protein": 7, "carbs": 7, "fat": 18},
            0.05,
        )

    def test_big_calorie_fix_is_different(self):
        # Peanut butter case: stated 6080 → corrected 218
        assert _macros_differ(
            {"calories": 218, "protein": 7, "carbs": 7, "fat": 18},
            {"calories": 6080, "protein": 7, "carbs": 7, "fat": 18},
            0.05,
        )

    def test_protein_correction_is_different(self):
        # Bread case: stated protein 2.7g → corrected 4g
        assert _macros_differ(
            {"calories": 110, "protein": 4, "carbs": 22, "fat": 1.5},
            {"calories": 110, "protein": 2.7, "carbs": 22, "fat": 1.5},
            0.05,
        )

    def test_near_zero_values_ignored(self):
        # Truly-zero product (e.g. water). Per-field values are <1 on both
        # sides, so the near-zero guard short-circuits each macro check.
        assert not _macros_differ(
            {"calories": 0, "protein": 0, "carbs": 0, "fat": 0},
            {"calories": 0, "protein": 0, "carbs": 0, "fat": 0},
            0.05,
        )
        # One side reports a trace amount below 1 on every macro - also OK.
        assert not _macros_differ(
            {"calories": 0.5, "protein": 0.1, "carbs": 0.2, "fat": 0},
            {"calories": 0, "protein": 0, "carbs": 0, "fat": 0},
            0.05,
        )

    def test_asymmetric_one_side_zero(self):
        # bv=0, av=10 is a 100% difference - must flag. Denom uses
        # max(abs(bv), 1.0) = 1 so the ratio is 10.0, well above 0.05.
        # This case was missed in the original test suite.
        assert _macros_differ(
            {"calories": 10, "protein": 1, "carbs": 1, "fat": 0},
            {"calories": 0, "protein": 0, "carbs": 0, "fat": 0},
            0.05,
        )
        # And the reverse direction: original non-zero, final zero - also
        # should flag (user zero'd out a meaningful field).
        assert _macros_differ(
            {"calories": 0, "protein": 0, "carbs": 0, "fat": 0},
            {"calories": 100, "protein": 10, "carbs": 10, "fat": 2},
            0.05,
        )


# ── DB round-trip for barcode corrections ──────────────────────────

@pytest.fixture
async def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    await ensure_user(db_path, user_id=1, username="test", first_name="Test")
    return db_path


@pytest.mark.asyncio
async def test_save_and_get_barcode_correction(db):
    await save_barcode_correction(
        db_path=db, user_id=1, barcode="099482450267",
        product_name="Peanut Butter", brand="365",
        calories=190.0, protein=7.0, carbs=7.0, fat=18.0,
        serving_size_g=32.0, serving_label="2 Tbsp (32 g)",
    )
    row = await get_barcode_correction(db, 1, "099482450267")
    assert row is not None
    assert row["calories"] == 190.0
    assert row["protein"] == 7.0
    assert row["product_name"] == "Peanut Butter"
    assert row["corrected_at"]  # timestamp populated


@pytest.mark.asyncio
async def test_correction_is_per_user(db):
    """A correction saved for user 1 must not leak to user 2."""
    await ensure_user(db, user_id=2, username="test2", first_name="Two")
    await save_barcode_correction(
        db_path=db, user_id=1, barcode="123456789",
        product_name="X", brand="",
        calories=100, protein=10, carbs=10, fat=2,
        serving_size_g=50, serving_label="",
    )
    assert await get_barcode_correction(db, 1, "123456789") is not None
    assert await get_barcode_correction(db, 2, "123456789") is None


@pytest.mark.asyncio
async def test_correction_upsert_overwrites(db):
    """Saving twice for the same (user, barcode) replaces the earlier row."""
    await save_barcode_correction(
        db_path=db, user_id=1, barcode="123",
        product_name="First", brand="",
        calories=100, protein=10, carbs=10, fat=2,
        serving_size_g=50, serving_label="",
    )
    await save_barcode_correction(
        db_path=db, user_id=1, barcode="123",
        product_name="Second", brand="",
        calories=200, protein=20, carbs=20, fat=4,
        serving_size_g=50, serving_label="",
    )
    row = await get_barcode_correction(db, 1, "123")
    assert row["product_name"] == "Second"
    assert row["calories"] == 200


@pytest.mark.asyncio
async def test_delete_barcode_correction(db):
    await save_barcode_correction(
        db_path=db, user_id=1, barcode="456",
        product_name="X", brand="",
        calories=100, protein=10, carbs=10, fat=2,
        serving_size_g=50, serving_label="",
    )
    removed = await delete_barcode_correction(db, 1, "456")
    assert removed is True
    assert await get_barcode_correction(db, 1, "456") is None
    # Second delete returns False
    assert await delete_barcode_correction(db, 1, "456") is False


# ── _maybe_save_barcode_correction scope/threshold integration ─────

def _make_session(barcode: str, original: dict) -> dict:
    import json as _json
    return {
        "barcode": barcode,
        "original_nutrition": _json.dumps(original),
    }


def _make_result(name: str, cal: float, p: float, c: float, f: float, weight_g: float = 32.0) -> NutritionResult:
    item = FoodItem(
        name=name, description="", brand=None, has_label=False,
        calories=cal, protein=p, carbs=c, fat=f, weight_g=weight_g, source="barcode",
    )
    return NutritionResult(
        item_name=name, meal_description="", items=[item],
        calories=cal, protein=p, carbs=c, fat=f, source="barcode",
    )


@pytest.mark.asyncio
async def test_maybe_save_correction_saves_when_macros_differ(db):
    """User corrects peanut butter from 6080 cal to 190 cal - should save."""
    session = _make_session("099482450267", {"calories": 6080, "protein": 7, "carbs": 7, "fat": 18})
    result = _make_result("Peanut Butter", 190, 7, 7, 18)
    await _maybe_save_barcode_correction(db, 1, session, result, servings=1.0)
    saved = await get_barcode_correction(db, 1, "099482450267")
    assert saved is not None
    assert saved["calories"] == 190.0


@pytest.mark.asyncio
async def test_maybe_save_correction_skips_when_below_threshold(db):
    """User didn't meaningfully edit - nothing to save."""
    session = _make_session("abc", {"calories": 190, "protein": 7, "carbs": 7, "fat": 18})
    result = _make_result("Peanut Butter", 192, 7, 7, 18)  # 1% diff
    await _maybe_save_barcode_correction(db, 1, session, result, servings=1.0)
    assert await get_barcode_correction(db, 1, "abc") is None


@pytest.mark.asyncio
async def test_maybe_save_correction_skips_without_barcode(db):
    """Non-barcode sessions should never save a correction."""
    session = {"barcode": "", "original_nutrition": ""}
    result = _make_result("X", 500, 20, 40, 10)
    await _maybe_save_barcode_correction(db, 1, session, result, servings=1.0)
    # Nothing to look up since there's no barcode


@pytest.mark.asyncio
async def test_maybe_save_correction_skips_multi_item_result(db):
    """If the user split the meal into multiple items (e.g. scanned a
    drink barcode and added a snack in the AI correction chat), the
    result isn't a 'correction to this barcode' - it's a composition.
    _maybe_save_barcode_correction must silently skip so we don't
    overwrite the correction with the first item's macros."""
    session = _make_session("1234567890", {"calories": 100, "protein": 5, "carbs": 10, "fat": 2})
    # Hand-build a multi-item NutritionResult (two items)
    item1 = FoodItem(
        name="Drink", description="", brand=None, has_label=False,
        calories=100, protein=5, carbs=10, fat=2, weight_g=200, source="barcode",
    )
    item2 = FoodItem(
        name="Snack", description="", brand=None, has_label=False,
        calories=150, protein=3, carbs=20, fat=6, weight_g=30, source="edited",
    )
    result = NutritionResult(
        item_name="Drink + snack", meal_description="", items=[item1, item2],
        calories=250, protein=8, carbs=30, fat=8, source="barcode",
    )
    await _maybe_save_barcode_correction(db, 1, session, result, servings=1.0)
    # No correction saved despite the macros differing wildly from the original
    assert await get_barcode_correction(db, 1, "1234567890") is None


@pytest.mark.asyncio
async def test_maybe_save_correction_backs_out_servings(db):
    """If the user ate 2 servings, the saved correction should be per-serving
    (1x), not double."""
    session = _make_session("xyz", {"calories": 100, "protein": 5, "carbs": 10, "fat": 2})
    # User reports eating 2 servings of a 250-kcal product
    result = _make_result("Thing", 500, 20, 30, 10, weight_g=100)
    await _maybe_save_barcode_correction(db, 1, session, result, servings=2.0)
    saved = await get_barcode_correction(db, 1, "xyz")
    assert saved is not None
    assert saved["calories"] == 250.0  # 500 / 2
    assert saved["protein"] == 10.0    # 20 / 2
    assert saved["serving_size_g"] == 50.0  # 100g / 2 servings
