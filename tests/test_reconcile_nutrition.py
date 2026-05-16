"""Tests for `_reconcile_nutrition_totals` - fixes Gemini's arithmetic drift
between top-level totals and the sum of `items[*]`.
"""

from src.gemini import _reconcile_nutrition_totals
from src.models import FoodItem, NutritionResult


def _item(name: str, cal: float, p: float, c: float, f: float) -> FoodItem:
    return FoodItem(
        name=name,
        description="",
        calories=cal,
        protein=p,
        carbs=c,
        fat=f,
        weight_g=100,
    )


def test_drift_above_tolerance_replaced():
    """The real meal_id=2735 case: top-level protein 71.52, items sum 40.47.
    Should be reconciled to the items sum.
    """
    result = NutritionResult(
        item_name="Chicken and Rice",
        items=[
            _item("Yellow Rice", 260, 5.4, 56.4, 0.6),
            _item("Grilled Chicken", 165, 31.0, 0, 3.6),
            _item("Caramelized Onions", 30, 0.5, 7.0, 0.05),
            _item("Chopped Salad", 12, 0.72, 2.72, 0.08),
            _item("Red Curry Sauce", 45, 0.6, 1.5, 3.9),
            _item("Yellow Lentil Dal", 30, 2.1, 4.5, 0.6),
            _item("Green Chutney", 15, 0.15, 1.5, 0.9),
        ],
        calories=557,
        protein=71.52,   # Gemini's broken sum
        carbs=73.12,
        fat=9.73,
    )
    fixed = _reconcile_nutrition_totals(result)
    assert fixed.protein == 40.47
    # Other macros are within tolerance, should stay
    assert fixed.calories == 557
    assert fixed.fat == 9.73


def test_drift_within_tolerance_kept():
    """If the drift is below 1%, leave the top-level alone - it might just be
    rounding from the model.
    """
    result = NutritionResult(
        item_name="m",
        items=[_item("a", 100, 10, 20, 5), _item("b", 100, 10, 20, 5)],
        calories=200.5,   # +0.25%
        protein=20,
        carbs=40,
        fat=10,
    )
    fixed = _reconcile_nutrition_totals(result)
    assert fixed.calories == 200.5  # untouched


def test_empty_items_noop():
    """Text-only meals or 'Unknown' fallbacks may have no items breakdown.
    Reconciliation must not blow them away.
    """
    result = NutritionResult(
        item_name="Unknown",
        items=[],
        calories=300,
        protein=20,
        carbs=30,
        fat=10,
    )
    fixed = _reconcile_nutrition_totals(result)
    assert fixed.calories == 300
    assert fixed.protein == 20


def test_only_one_macro_drifts():
    """Only the macro that's off should change."""
    result = NutritionResult(
        item_name="m",
        items=[_item("a", 100, 5, 20, 5)],
        calories=100,
        protein=99,    # broken
        carbs=20,
        fat=5,
    )
    fixed = _reconcile_nutrition_totals(result)
    assert fixed.protein == 5
    assert fixed.calories == 100
    assert fixed.carbs == 20
    assert fixed.fat == 5


def test_zero_totals_noop():
    """Black-coffee case: items sum to 0 and top is 0 - no change, no
    division-by-zero.
    """
    result = NutritionResult(
        item_name="Black Coffee",
        items=[_item("Coffee", 0, 0, 0, 0)],
        calories=0,
        protein=0,
        carbs=0,
        fat=0,
    )
    fixed = _reconcile_nutrition_totals(result)
    assert fixed.calories == 0
    assert fixed.protein == 0


def test_already_consistent_kept():
    """No drift → return same object (no spurious model_copy)."""
    items = [_item("a", 100, 10, 20, 5), _item("b", 100, 10, 20, 5)]
    result = NutritionResult(
        item_name="m",
        items=items,
        calories=200,
        protein=20,
        carbs=40,
        fat=10,
    )
    fixed = _reconcile_nutrition_totals(result)
    assert fixed is result
