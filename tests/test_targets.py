"""Tests for target-setting logic: parser, sanity checks, Mifflin-St Jeor, context builder."""

import json
from types import SimpleNamespace

import pytest

from src.gemini import _response_to_targets


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(**overrides):
    """Create a mock TargetSuggestRequest with sensible defaults."""
    defaults = {
        "age": 30,
        "sex": "male",
        "weight_kg": 80.0,
        "height_cm": 178.0,
        "goal": "maintain",
        "activity_level": "lightly_active",
        "workouts_per_week": 3,
        "weight_change_rate_kg": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _valid_json(**overrides):
    """Return a valid target JSON string. Override individual fields."""
    data = {
        "calories": 2200,
        "protein": 160,
        "carbs": 220,
        "fat": 73,
        "explanation": "Based on your stats, here is your plan.",
        "profile": {"age": 30, "weight_kg": 80, "height_cm": 178, "sex": "male"},
    }
    data.update(overrides)
    return json.dumps(data)


# ---------------------------------------------------------------------------
# Import the route helper we're testing (it's a plain function, no FastAPI deps)
# ---------------------------------------------------------------------------
from src.web.routes.targets import _mifflin_st_jeor, _build_context


# ===========================================================================
# _response_to_targets - parser + sanity checks
# ===========================================================================

class TestResponseToTargets:
    """Tests for _response_to_targets in src/gemini.py."""

    # 1. Valid JSON with all positive values -> returns parsed dict
    def test_valid_json_all_positive(self):
        result = _response_to_targets(_valid_json())
        assert result is not None
        assert result["calories"] == 2200
        assert result["protein"] == 160
        assert result["carbs"] == 220
        assert result["fat"] == 73
        assert result["explanation"] == "Based on your stats, here is your plan."

    # 2. JSON with null values -> returns None
    def test_json_with_null_values(self):
        text = json.dumps({"calories": None, "protein": 160, "carbs": 220, "fat": 73})
        assert _response_to_targets(text) is None

    # 3. JSON with zero values -> returns None
    def test_json_with_zero_values(self):
        text = json.dumps({"calories": 2200, "protein": 0, "carbs": 220, "fat": 73})
        assert _response_to_targets(text) is None

    # 4. Calories below 1200 -> returns None
    def test_calories_below_minimum(self):
        text = _valid_json(calories=1100, protein=80, carbs=100, fat=30)
        assert _response_to_targets(text) is None

    # 5. Calories above 6000 -> returns None
    def test_calories_above_maximum(self):
        text = _valid_json(calories=6500, protein=200, carbs=400, fat=150)
        assert _response_to_targets(text) is None

    # 6. Protein below 30 -> returns None
    def test_protein_below_minimum(self):
        text = _valid_json(protein=25)
        assert _response_to_targets(text) is None

    # 7. Protein above 400 -> returns None
    def test_protein_above_maximum(self):
        text = _valid_json(protein=450)
        assert _response_to_targets(text) is None

    # 8. Fat below 15 -> returns None
    def test_fat_below_minimum(self):
        text = _valid_json(fat=10)
        assert _response_to_targets(text) is None

    # 9. Macro-calorie mismatch >20% -> auto-corrects calories
    def test_macro_calorie_mismatch_autocorrects(self):
        # protein=150, carbs=250, fat=80: computed = 150*4 + 250*4 + 80*9 = 600+1000+720 = 2320
        # stated calories=1800 -> mismatch = |2320-1800|/1800 = 28.9% > 20%
        text = _valid_json(calories=1800, protein=150, carbs=250, fat=80)
        result = _response_to_targets(text)
        assert result is not None
        expected_computed = 150 * 4 + 250 * 4 + 80 * 9  # 2320
        assert result["calories"] == round(expected_computed)

    # 10. Macro-calorie mismatch within 20% -> keeps stated calories
    def test_macro_calorie_mismatch_within_tolerance(self):
        # protein=160, carbs=220, fat=73: computed = 640+880+657 = 2177
        # stated calories=2200 -> mismatch = |2177-2200|/2200 = 1.05% < 20%
        text = _valid_json(calories=2200, protein=160, carbs=220, fat=73)
        result = _response_to_targets(text)
        assert result is not None
        assert result["calories"] == 2200  # kept as-is

    # 11. Valid JSON with profile data -> profile extracted correctly
    def test_profile_extracted(self):
        text = _valid_json(profile={"age": 25, "weight_kg": 70.5, "height_cm": 165, "sex": "female"})
        result = _response_to_targets(text)
        assert result is not None
        assert result["profile"]["age"] == 25
        assert result["profile"]["weight_kg"] == 70.5
        assert result["profile"]["height_cm"] == 165
        assert result["profile"]["sex"] == "female"

    # 12. Valid JSON without profile -> profile has nulls
    def test_no_profile_gives_nulls(self):
        data = {"calories": 2200, "protein": 160, "carbs": 220, "fat": 73}
        text = json.dumps(data)
        result = _response_to_targets(text)
        assert result is not None
        assert result["profile"]["age"] is None
        assert result["profile"]["weight_kg"] is None
        assert result["profile"]["height_cm"] is None
        assert result["profile"]["sex"] is None

    # 13. Plain text (no JSON) -> returns None
    def test_plain_text_returns_none(self):
        assert _response_to_targets("I need more info about your goals.") is None

    # 14. JSON wrapped in markdown code fence -> still parses
    def test_json_in_code_fence(self):
        text = '```json\n{"calories": 2200, "protein": 160, "carbs": 220, "fat": 73}\n```'
        result = _response_to_targets(text)
        assert result is not None
        assert result["calories"] == 2200
        assert result["protein"] == 160



# ===========================================================================
# _mifflin_st_jeor - deterministic fallback
# ===========================================================================

class TestMifflinStJeor:
    """Tests for _mifflin_st_jeor in src/web/routes/targets.py."""

    # 20. Male with full profile -> correct BMR calculation
    def test_male_full_profile(self):
        req = _make_request(sex="male", weight_kg=80, height_cm=178, age=30,
                            goal="maintain", activity_level="lightly_active")
        result = _mifflin_st_jeor(req)
        assert result is not None

        # BMR = 10*80 + 6.25*178 - 5*30 + 5 = 800 + 1112.5 - 150 + 5 = 1767.5
        expected_bmr = 1767.5
        expected_tdee = expected_bmr * 1.375  # lightly_active
        assert result["calories"] == round(expected_tdee)

    # 21. Female with full profile -> correct BMR calculation
    def test_female_full_profile(self):
        req = _make_request(sex="female", weight_kg=60, height_cm=165, age=25,
                            goal="maintain", activity_level="lightly_active")
        result = _mifflin_st_jeor(req)
        assert result is not None

        # BMR = 10*60 + 6.25*165 - 5*25 - 161 = 600 + 1031.25 - 125 - 161 = 1345.25
        expected_bmr = 1345.25
        expected_tdee = expected_bmr * 1.375
        assert result["calories"] == round(expected_tdee)

    # 22. Lose weight goal -> calories below TDEE
    def test_lose_weight_below_tdee(self):
        req = _make_request(sex="male", weight_kg=90, height_cm=180, age=35,
                            goal="lose_weight", activity_level="active",
                            weight_change_rate_kg=0.5)
        result = _mifflin_st_jeor(req)
        assert result is not None

        bmr = 10 * 90 + 6.25 * 180 - 5 * 35 + 5  # 900 + 1125 - 175 + 5 = 1855
        tdee = bmr * 1.55
        assert result["calories"] < tdee

    # 23. Gain weight goal -> calories above TDEE
    def test_gain_weight_above_tdee(self):
        req = _make_request(sex="male", weight_kg=70, height_cm=175, age=28,
                            goal="gain_weight", activity_level="active",
                            weight_change_rate_kg=0.3)
        result = _mifflin_st_jeor(req)
        assert result is not None

        bmr = 10 * 70 + 6.25 * 175 - 5 * 28 + 5  # 700 + 1093.75 - 140 + 5 = 1658.75
        tdee = bmr * 1.55
        assert result["calories"] > tdee

    # 24. Maintain goal -> calories equal TDEE
    def test_maintain_equals_tdee(self):
        req = _make_request(sex="male", weight_kg=80, height_cm=178, age=30,
                            goal="maintain", activity_level="lightly_active")
        result = _mifflin_st_jeor(req)
        assert result is not None

        bmr = 10 * 80 + 6.25 * 178 - 5 * 30 + 5
        tdee = bmr * 1.375
        assert result["calories"] == round(tdee)

    # 25. Missing weight/height/age -> returns None
    def test_missing_weight_returns_none(self):
        req = _make_request(weight_kg=None)
        assert _mifflin_st_jeor(req) is None

    def test_missing_height_returns_none(self):
        req = _make_request(height_cm=None)
        assert _mifflin_st_jeor(req) is None

    def test_missing_age_returns_none(self):
        req = _make_request(age=None)
        assert _mifflin_st_jeor(req) is None

    # 26. Calorie floor enforced (never below 1200 for women)
    def test_calorie_floor_for_women(self):
        # Very aggressive weight loss for a small woman - should hit the floor
        req = _make_request(sex="female", weight_kg=50, height_cm=155, age=40,
                            goal="lose_weight", activity_level="sedentary",
                            weight_change_rate_kg=1.5)
        result = _mifflin_st_jeor(req)
        assert result is not None
        assert result["calories"] >= 1200

    def test_calorie_floor_for_men(self):
        # Very aggressive weight loss for a light man - should hit the 1500 floor
        req = _make_request(sex="male", weight_kg=60, height_cm=165, age=40,
                            goal="lose_weight", activity_level="sedentary",
                            weight_change_rate_kg=1.5)
        result = _mifflin_st_jeor(req)
        assert result is not None
        assert result["calories"] >= 1500

    # 27. Different activity levels produce different TDEE values
    def test_activity_levels_differ(self):
        base = {"sex": "male", "weight_kg": 80, "height_cm": 178, "age": 30, "goal": "maintain"}
        results = {}
        for level in ("sedentary", "lightly_active", "active", "very_active"):
            req = _make_request(**base, activity_level=level)
            r = _mifflin_st_jeor(req)
            assert r is not None
            results[level] = r["calories"]

        assert results["sedentary"] < results["lightly_active"]
        assert results["lightly_active"] < results["active"]
        assert results["active"] < results["very_active"]

    # Extra: protein scales with body weight and goal
    def test_protein_scales_with_goal(self):
        req_lose = _make_request(goal="lose_weight", weight_change_rate_kg=0.5)
        req_gain = _make_request(goal="gain_weight", weight_change_rate_kg=0.3)
        req_maintain = _make_request(goal="maintain")

        r_lose = _mifflin_st_jeor(req_lose)
        r_gain = _mifflin_st_jeor(req_gain)
        r_maintain = _mifflin_st_jeor(req_maintain)

        # Lose weight gets highest protein multiplier (2.0), maintain gets lowest (1.6)
        assert r_lose["protein"] > r_maintain["protein"]
        assert r_gain["protein"] > r_maintain["protein"]

    # Extra: macros are internally consistent (protein*4 + carbs*4 + fat*9 ~ calories)
    def test_macros_consistent(self):
        req = _make_request()
        result = _mifflin_st_jeor(req)
        assert result is not None
        computed = result["protein"] * 4 + result["carbs"] * 4 + result["fat"] * 9
        # Due to rounding, allow small deviation
        assert abs(computed - result["calories"]) < 40

    # Extra: explanation mentions BMR
    def test_explanation_mentions_bmr(self):
        req = _make_request()
        result = _mifflin_st_jeor(req)
        assert result is not None
        assert "BMR" in result["explanation"]
        assert "TDEE" in result["explanation"]


# ===========================================================================
# _build_context - context string builder
# ===========================================================================

class TestBuildContext:
    """Tests for _build_context in src/web/routes/targets.py."""

    # 28. Full request -> all fields present in context string
    def test_full_request(self):
        from src.web.schemas import TargetSuggestRequest
        req = TargetSuggestRequest(
            age=30, sex="male", weight_kg=80.0, height_cm=178.0,
            goal="lose_weight", activity_level="active",
            workouts_per_week=4, weight_change_rate_kg=0.5,
        )
        ctx = _build_context(req)

        assert "30-year-old" in ctx
        assert "male" in ctx
        assert "178cm" in ctx
        assert "80.0kg" in ctx
        assert "lose weight" in ctx
        assert "active" in ctx
        assert "4 sessions per week" in ctx
        assert "deficit" in ctx
        assert "0.50 kg/week" in ctx

    # 29. Minimal request -> graceful with missing fields
    def test_minimal_request(self):
        from src.web.schemas import TargetSuggestRequest
        req = TargetSuggestRequest(goal="maintain", activity_level="sedentary")
        ctx = _build_context(req)

        assert "maintain weight" in ctx
        assert "sedentary" in ctx
        # No crash, no "None" text
        assert "None" not in ctx

    # 30. Weight change rate included for non-maintain goals
    def test_weight_change_rate_for_gain(self):
        from src.web.schemas import TargetSuggestRequest
        req = TargetSuggestRequest(
            goal="gain_weight", activity_level="lightly_active",
            weight_change_rate_kg=0.3,
        )
        ctx = _build_context(req)

        assert "surplus" in ctx
        assert "0.30 kg/week" in ctx

    def test_no_weight_change_rate_for_maintain(self):
        from src.web.schemas import TargetSuggestRequest
        req = TargetSuggestRequest(
            goal="maintain", activity_level="lightly_active",
            weight_change_rate_kg=0.5,
        )
        ctx = _build_context(req)

        # For maintain goal, weight change rate is not included
        assert "deficit" not in ctx
        assert "surplus" not in ctx


# ===========================================================================
# Edge cases
# ===========================================================================

class TestEdgeCases:
    """Additional edge cases for robustness."""

    def test_response_to_targets_negative_values(self):
        """Negative macro values should be rejected."""
        text = json.dumps({"calories": 2200, "protein": -50, "carbs": 220, "fat": 73})
        assert _response_to_targets(text) is None

    def test_response_to_targets_empty_json(self):
        """Empty JSON object should return None."""
        assert _response_to_targets("{}") is None

    def test_response_to_targets_profile_sex_null_string(self):
        """Sex value of 'null' string should become None."""
        text = _valid_json(profile={"age": 30, "weight_kg": 80, "height_cm": 178, "sex": "null"})
        result = _response_to_targets(text)
        assert result is not None
        assert result["profile"]["sex"] is None

    def test_response_to_targets_carbs_above_max(self):
        """Carbs above 800 should be rejected."""
        text = _valid_json(carbs=850, calories=5000, protein=200, fat=100)
        assert _response_to_targets(text) is None

    def test_response_to_targets_fat_above_max(self):
        """Fat above 300 should be rejected."""
        text = _valid_json(fat=350, calories=5000, protein=200, carbs=200)
        assert _response_to_targets(text) is None

    def test_mifflin_unknown_activity_defaults(self):
        """Unknown activity level uses 1.375 as default multiplier."""
        req_default = _make_request(activity_level="unknown_level")
        req_lightly = _make_request(activity_level="lightly_active")
        r1 = _mifflin_st_jeor(req_default)
        r2 = _mifflin_st_jeor(req_lightly)
        assert r1 is not None and r2 is not None
        assert r1["calories"] == r2["calories"]

    def test_mifflin_default_weight_change_rate(self):
        """When weight_change_rate_kg is None, default of 0.5 is used."""
        req = _make_request(goal="lose_weight", weight_change_rate_kg=None)
        result = _mifflin_st_jeor(req)
        assert result is not None
        # Should use 0.5 kg/week default
        bmr = 10 * 80 + 6.25 * 178 - 5 * 30 + 5
        tdee = bmr * 1.375
        daily_adjust = 0.5 * 7700 / 7
        expected = max(1500, tdee - daily_adjust)
        assert result["calories"] == round(expected)

    def test_build_context_only_age(self):
        """Context with only age still works."""
        from src.web.schemas import TargetSuggestRequest
        req = TargetSuggestRequest(age=25)
        ctx = _build_context(req)
        assert "25-year-old" in ctx
        assert "None" not in ctx
