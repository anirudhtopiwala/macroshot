"""Tests for src/gemini.py - pure parsing helpers (no API calls)."""

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.gemini import (
    _count_web_searches,
    _extract_json,
    _extract_response_text,
    _parse_nutrition_response,
    _response_to_nutrition_result,
    _response_to_targets,
    gemini_analyze_meal,
    gemini_eod_check,
    gemini_suggest_targets,
)


# ---------------------------------------------------------------------------
# Helpers for building fake Gemini response objects
# ---------------------------------------------------------------------------

def _make_part(text=None, has_function_call=False):
    """Build a fake Gemini response Part."""
    if has_function_call:
        # Parts with function_call have .text == None
        return SimpleNamespace(text=None, function_call=SimpleNamespace(name="google_search"))
    return SimpleNamespace(text=text)


def _make_response(parts_spec, web_search_queries=None, prompt_tokens=50, output_tokens=30):
    """Build a minimal fake Gemini response.

    parts_spec: list of dicts, e.g. [{"text": "..."}, {"function_call": True}]
    """
    parts = []
    for spec in parts_spec:
        if spec.get("function_call"):
            parts.append(_make_part(has_function_call=True))
        else:
            parts.append(_make_part(text=spec.get("text")))

    grounding_metadata = SimpleNamespace(
        web_search_queries=web_search_queries if web_search_queries is not None else []
    )
    content = SimpleNamespace(parts=parts)
    candidate = SimpleNamespace(content=content, grounding_metadata=grounding_metadata)
    usage_metadata = SimpleNamespace(
        prompt_token_count=prompt_tokens,
        candidates_token_count=output_tokens,
    )
    # response.text mirrors first text part (used as fallback)
    first_text = next((p.text for p in parts if p.text), None)
    return SimpleNamespace(
        candidates=[candidate],
        usage_metadata=usage_metadata,
        text=first_text,
    )


_VALID_JSON = json.dumps({
    "item_name": "Pizza Margherita",
    "meal_description": "Classic pizza with tomato and mozzarella",
    "items": [
        {"name": "Pizza", "description": "2 slices ~220g", "calories": 570,
         "protein": 24, "carbs": 72, "fat": 20}
    ],
    "calories": 570,
    "protein": 24,
    "carbs": 72,
    "fat": 20,
})


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:

    def test_plain_json(self):
        raw = '{"calories": 500, "protein": 40}'
        assert _extract_json(raw) == {"calories": 500, "protein": 40}

    def test_json_with_markdown_block(self):
        text = "```json\n{\"calories\": 500}\n```"
        assert _extract_json(text) == {"calories": 500}

    def test_json_with_unmarked_code_block(self):
        text = "```\n{\"calories\": 500}\n```"
        assert _extract_json(text) == {"calories": 500}

    def test_json_with_preamble_text(self):
        text = 'Here is the analysis:\n{"calories": 500}\nHope that helps!'
        assert _extract_json(text) == {"calories": 500}

    def test_json_with_trailing_text(self):
        text = '{"calories": 500} Let me know if you have questions.'
        assert _extract_json(text) == {"calories": 500}

    def test_nested_json(self):
        text = '{"items": [{"name": "rice", "calories": 200}], "calories": 200}'
        result = _extract_json(text)
        assert result["calories"] == 200
        assert result["items"][0]["name"] == "rice"

    def test_raises_on_no_json(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            _extract_json("Sorry, I cannot help with that.")

    def test_raises_on_empty_string(self):
        with pytest.raises(ValueError):
            _extract_json("")

    def test_raises_on_unbalanced_braces(self):
        with pytest.raises((ValueError, Exception)):
            _extract_json("{unclosed")

    def test_unicode_values(self):
        text = '{"item_name": "Crêpe", "calories": 300}'
        result = _extract_json(text)
        assert result["item_name"] == "Crêpe"

    def test_whitespace_only_before_json(self):
        text = "   \n\n   {\"calories\": 100}"
        assert _extract_json(text) == {"calories": 100}


# ---------------------------------------------------------------------------
# _parse_nutrition_response
# ---------------------------------------------------------------------------

class TestParseNutritionResponse:

    def _valid_raw(self, **overrides):
        base = {
            "item_name": "Chicken Rice",
            "meal_description": "Grilled chicken with rice",
            "items": [
                {"name": "Chicken", "description": "150g", "calories": 250, "protein": 35, "carbs": 0, "fat": 8},
                {"name": "Rice", "description": "200g", "calories": 260, "protein": 5, "carbs": 55, "fat": 1},
            ],
            "calories": 510,
            "protein": 40,
            "carbs": 55,
            "fat": 9,
        }
        base.update(overrides)
        return base

    def test_valid_full_response(self):
        nr = _parse_nutrition_response(self._valid_raw())
        assert nr.item_name == "Chicken Rice"
        assert nr.calories == pytest.approx(510.0)
        assert len(nr.items) == 2
        assert nr.items[0].name == "Chicken"

    def test_missing_item_name_defaults_to_unknown(self):
        raw = self._valid_raw(item_name="")
        nr = _parse_nutrition_response(raw)
        assert nr.item_name == "Unknown"

    def test_none_item_name_defaults_to_unknown(self):
        raw = self._valid_raw(item_name=None)
        nr = _parse_nutrition_response(raw)
        assert nr.item_name == "Unknown"

    def test_missing_items_gives_empty_list(self):
        raw = self._valid_raw()
        del raw["items"]
        nr = _parse_nutrition_response(raw)
        assert nr.items == []

    def test_string_numbers_are_coerced(self):
        raw = self._valid_raw(calories="600", protein="45.5")
        nr = _parse_nutrition_response(raw)
        assert nr.calories == pytest.approx(600.0)
        assert nr.protein == pytest.approx(45.5)

    def test_negative_macros_clamped_to_zero(self):
        raw = self._valid_raw(calories=-100, protein=-5)
        nr = _parse_nutrition_response(raw)
        assert nr.calories == 0.0
        assert nr.protein == 0.0

    def test_item_with_bad_data_is_coerced_not_skipped(self):
        # _coerce_float("not-a-number") returns 0.0 rather than raising,
        # so the item is kept with calories=0 rather than dropped.
        raw = self._valid_raw()
        raw["items"].append({"name": "BadItem", "calories": "not-a-number"})
        nr = _parse_nutrition_response(raw)
        assert len(nr.items) == 3
        assert nr.items[2].name == "BadItem"
        assert nr.items[2].calories == pytest.approx(0.0)

    def test_item_with_empty_name_becomes_unknown(self):
        raw = self._valid_raw()
        raw["items"] = [{"name": "", "calories": 100, "protein": 5, "carbs": 10, "fat": 3}]
        nr = _parse_nutrition_response(raw)
        assert nr.items[0].name == "Unknown"

    def test_missing_meal_description_is_empty_string(self):
        raw = self._valid_raw()
        del raw["meal_description"]
        nr = _parse_nutrition_response(raw)
        assert nr.meal_description == ""

    # --- weight_g and per_100g (new fields) ---

    def test_weight_g_extracted_per_item(self):
        raw = self._valid_raw()
        raw["items"][0]["weight_g"] = 150
        raw["items"][1]["weight_g"] = 200
        nr = _parse_nutrition_response(raw)
        assert nr.items[0].weight_g == pytest.approx(150.0)
        assert nr.items[1].weight_g == pytest.approx(200.0)

    def test_weight_g_none_when_absent(self):
        nr = _parse_nutrition_response(self._valid_raw())
        for item in nr.items:
            assert item.weight_g is None

    def test_weight_g_float_coercion(self):
        raw = self._valid_raw()
        raw["items"][0]["weight_g"] = "175.5"
        nr = _parse_nutrition_response(raw)
        assert nr.items[0].weight_g == pytest.approx(175.5)

    def test_per_100g_populates_gemini_per_100g(self):
        raw = self._valid_raw()
        raw["items"][0]["per_100g"] = {"calories": 166.7, "protein": 23.3, "carbs": 0, "fat": 5.3}
        nr = _parse_nutrition_response(raw)
        g = nr.items[0].gemini_per_100g
        assert g is not None
        assert g["calories"] == pytest.approx(166.7)
        assert g["protein"] == pytest.approx(23.3)
        assert g["carbs"] == pytest.approx(0.0)
        assert g["fat"] == pytest.approx(5.3)

    def test_per_100g_absent_leaves_gemini_per_100g_none(self):
        nr = _parse_nutrition_response(self._valid_raw())
        for item in nr.items:
            assert item.gemini_per_100g is None

    def test_per_100g_partial_fields_default_to_zero(self):
        """Only some per_100g fields present → missing ones default to 0."""
        raw = self._valid_raw()
        raw["items"][0]["per_100g"] = {"calories": 200}
        nr = _parse_nutrition_response(raw)
        g = nr.items[0].gemini_per_100g
        assert g is not None
        assert g["calories"] == pytest.approx(200.0)
        assert g["protein"] == pytest.approx(0.0)
        assert g["carbs"] == pytest.approx(0.0)
        assert g["fat"] == pytest.approx(0.0)

    def test_fatsecret_per_100g_starts_as_none(self):
        """FatSecret field is populated later by _apply_references, never by parsing."""
        raw = self._valid_raw()
        raw["items"][0]["weight_g"] = 150
        raw["items"][0]["per_100g"] = {"calories": 166, "protein": 23, "carbs": 0, "fat": 5}
        nr = _parse_nutrition_response(raw)
        assert nr.items[0].fatsecret_per_100g is None

    def test_source_field_parsed_from_item(self):
        """Source field is passed through from Gemini's per-item JSON."""
        raw = self._valid_raw()
        raw["items"][0]["source"] = "image"
        nr = _parse_nutrition_response(raw)
        assert nr.items[0].source == "image"

    def test_source_field_defaults_to_none(self):
        """Source defaults to None when not present in Gemini output."""
        raw = self._valid_raw()
        nr = _parse_nutrition_response(raw)
        assert nr.items[0].source is None


# ---------------------------------------------------------------------------
# _response_to_nutrition_result - weight/per_100g pass-through
# ---------------------------------------------------------------------------

class TestResponseToNutritionResultWeightFields:

    _VALID_WITH_WEIGHT = json.dumps({
        "item_name": "Chicken Rice",
        "meal_description": "Grilled chicken with rice",
        "items": [
            {
                "name": "Chicken", "description": "~150g grilled", "calories": 250,
                "protein": 35, "carbs": 0, "fat": 8,
                "weight_g": 150,
                "per_100g": {"calories": 166.7, "protein": 23.3, "carbs": 0, "fat": 5.3},
            },
            {
                "name": "Rice", "description": "~200g cooked", "calories": 260,
                "protein": 5, "carbs": 55, "fat": 1,
                "weight_g": 200,
                "per_100g": {"calories": 130, "protein": 2.5, "carbs": 27.5, "fat": 0.5},
            },
        ],
        "calories": 510,
        "protein": 40,
        "carbs": 55,
        "fat": 9,
    })

    def test_weight_g_passes_through(self):
        result = _response_to_nutrition_result(self._VALID_WITH_WEIGHT)
        assert result is not None
        assert result.items[0].weight_g == pytest.approx(150.0)
        assert result.items[1].weight_g == pytest.approx(200.0)

    def test_gemini_per_100g_passes_through(self):
        result = _response_to_nutrition_result(self._VALID_WITH_WEIGHT)
        assert result is not None
        g0 = result.items[0].gemini_per_100g
        assert g0 is not None
        assert g0["calories"] == pytest.approx(166.7)
        assert g0["protein"] == pytest.approx(23.3)

    def test_fatsecret_per_100g_is_none_after_parse(self):
        result = _response_to_nutrition_result(self._VALID_WITH_WEIGHT)
        assert result is not None
        for item in result.items:
            assert item.fatsecret_per_100g is None

    def test_macros_intact_when_weight_present(self):
        """Parsing doesn't recalculate macros - raw Gemini values are preserved."""
        result = _response_to_nutrition_result(self._VALID_WITH_WEIGHT)
        assert result is not None
        assert result.items[0].calories == pytest.approx(250.0)
        assert result.calories == pytest.approx(510.0)


# ---------------------------------------------------------------------------
# _response_to_nutrition_result
# ---------------------------------------------------------------------------

class TestResponseToNutritionResult:

    _VALID = (
        '{"item_name":"Salad","meal_description":"Green salad",'
        '"items":[{"name":"Lettuce","description":"100g","calories":15,'
        '"protein":1,"carbs":2,"fat":0}],'
        '"calories":15,"protein":1,"carbs":2,"fat":0}'
    )

    def test_valid_text_returns_result(self):
        result = _response_to_nutrition_result(self._VALID)
        assert result is not None
        assert result.item_name == "Salad"
        assert result.calories == pytest.approx(15.0)
        assert result.source == "Gemini"

    def test_custom_source_is_recorded(self):
        result = _response_to_nutrition_result(self._VALID, source="OpenAI")
        assert result is not None
        assert result.source == "OpenAI"

    def test_returns_none_on_invalid_text(self):
        assert _response_to_nutrition_result("No JSON here at all") is None

    def test_returns_none_on_empty_string(self):
        assert _response_to_nutrition_result("") is None

    def test_returns_none_on_json_missing_fields(self):
        # JSON present but missing required macro fields → Pydantic raises → None
        result = _response_to_nutrition_result('{"item_name":"X"}')
        # Either None or a result with 0 macros is acceptable
        if result is not None:
            assert result.calories == pytest.approx(0.0)

    def test_json_wrapped_in_markdown(self):
        text = "```json\n" + self._VALID + "\n```"
        result = _response_to_nutrition_result(text)
        assert result is not None
        assert result.item_name == "Salad"

    def test_json_with_surrounding_text(self):
        text = "Sure! Here is the breakdown:\n" + self._VALID + "\nLet me know if you need anything else."
        result = _response_to_nutrition_result(text)
        assert result is not None
        assert result.calories == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# _response_to_targets
# ---------------------------------------------------------------------------

class TestResponseToTargets:

    _VALID = '{"calories":2000,"protein":150,"carbs":200,"fat":70,"explanation":"Standard cut."}'

    def test_valid_returns_dict(self):
        t = _response_to_targets(self._VALID)
        assert t is not None
        assert t["calories"] == pytest.approx(2000.0)
        assert t["protein"] == pytest.approx(150.0)
        assert t["explanation"] == "Standard cut."

    def test_missing_calories_returns_none(self):
        text = '{"protein":150,"carbs":200,"fat":70,"explanation":"x"}'
        assert _response_to_targets(text) is None

    def test_zero_calories_returns_none(self):
        text = '{"calories":0,"protein":150,"carbs":200,"fat":70}'
        assert _response_to_targets(text) is None

    def test_returns_none_on_garbage(self):
        assert _response_to_targets("not json") is None

    def test_markdown_wrapped(self):
        text = f"```json\n{self._VALID}\n```"
        t = _response_to_targets(text)
        assert t is not None
        assert t["calories"] == pytest.approx(2000.0)


# ---------------------------------------------------------------------------
# _extract_response_text
# ---------------------------------------------------------------------------

class TestExtractResponseText:
    """Tests for _extract_response_text with dummy response objects (no API calls)."""

    def test_plain_text_part(self):
        resp = _make_response([{"text": '{"calories": 500}'}])
        assert _extract_response_text(resp) == '{"calories": 500}'

    def test_function_call_part_only_returns_empty(self):
        """Simulates grounded response where model emits only tool-call parts - no text."""
        resp = _make_response([{"function_call": True}])
        assert _extract_response_text(resp) == ""

    def test_mixed_function_call_and_text_returns_text(self):
        """Grounded response: [function_call_part, text_part] - should return only text."""
        resp = _make_response([{"function_call": True}, {"text": '{"calories": 285}'}])
        assert _extract_response_text(resp) == '{"calories": 285}'

    def test_strips_tool_code_blocks(self):
        raw = 'Prefix <tool_code type="x">ignored content</tool_code> {"calories": 100}'
        resp = _make_response([{"text": raw}])
        result = _extract_response_text(resp)
        assert "<tool_code" not in result
        assert "ignored content" not in result
        assert '{"calories": 100}' in result

    def test_multiple_text_parts_joined(self):
        resp = _make_response([{"text": "Part one."}, {"text": "Part two."}])
        result = _extract_response_text(resp)
        assert "Part one." in result
        assert "Part two." in result

    def test_none_response_returns_empty(self):
        assert _extract_response_text(None) == ""

    def test_no_candidates_returns_empty(self):
        resp = SimpleNamespace(candidates=[], text=None)
        assert _extract_response_text(resp) == ""

    def test_conversational_grounded_text_is_returned_as_is(self):
        """When grounding causes model to return prose instead of JSON, text is returned."""
        prose = "I searched and found that pizza margherita has about 570 calories per two slices."
        resp = _make_response([{"text": prose}])
        result = _extract_response_text(resp)
        assert result == prose  # extracting prose - caller must handle non-JSON


# ---------------------------------------------------------------------------
# _count_web_searches
# ---------------------------------------------------------------------------

class TestCountWebSearches:
    """Tests for _count_web_searches with dummy response objects."""

    def test_no_queries_returns_zero(self):
        resp = _make_response([{"text": "hi"}], web_search_queries=[])
        assert _count_web_searches(resp) == 0

    def test_one_query_returns_one(self):
        resp = _make_response([{"text": "hi"}], web_search_queries=["pizza nutrition facts"])
        assert _count_web_searches(resp) == 1

    def test_multiple_queries(self):
        resp = _make_response([{"text": "hi"}], web_search_queries=["pizza", "mozzarella calories"])
        assert _count_web_searches(resp) == 2

    def test_no_grounding_metadata_returns_zero(self):
        # Build a response without grounding_metadata on the candidate
        candidate = SimpleNamespace(content=SimpleNamespace(parts=[]), grounding_metadata=None)
        resp = SimpleNamespace(candidates=[candidate])
        assert _count_web_searches(resp) == 0

    def test_queries_is_none_returns_zero(self):
        resp = _make_response([{"text": "hi"}], web_search_queries=None)
        assert _count_web_searches(resp) == 0

    def test_no_candidates_returns_zero(self):
        resp = SimpleNamespace(candidates=[])
        assert _count_web_searches(resp) == 0


# ---------------------------------------------------------------------------
# gemini_analyze_meal - mocked Gemini client
# ---------------------------------------------------------------------------

def _make_mock_client(responses):
    """Return a patched genai.Client whose aio context manager yields mock_aclient.

    responses: list of fake response objects returned sequentially by
               aclient.models.generate_content.
    """
    mock_aclient = AsyncMock()
    mock_aclient.models.generate_content = AsyncMock(side_effect=responses)

    mock_aio = MagicMock()
    mock_aio.__aenter__ = AsyncMock(return_value=mock_aclient)
    mock_aio.__aexit__ = AsyncMock(return_value=False)

    mock_client = MagicMock()
    mock_client.aio = mock_aio

    mock_client_cls = MagicMock(return_value=mock_client)
    return mock_client_cls, mock_aclient


@pytest.mark.asyncio
class TestGeminiAnalyzeMeal:
    """Integration tests for gemini_analyze_meal - no real API calls."""

    async def test_no_api_key_returns_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GEMINI_API_KEY", None)
            text, result = await gemini_analyze_meal([], user_text="pizza")
        assert text == ""
        assert result is None

    async def test_no_images_no_text_returns_empty(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            text, result = await gemini_analyze_meal([], user_text="")
        assert text == ""
        assert result is None

    async def test_plain_returns_valid_json_on_first_try(self):
        """Happy path: plain (non-grounded) first call returns parseable JSON → one API call."""
        resp = _make_response([{"text": _VALID_JSON}])
        client_cls, mock_aclient = _make_mock_client([resp])

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            text, result = await gemini_analyze_meal([], user_text="pizza margherita")

        assert result is not None
        assert result.item_name == "Pizza Margherita"
        assert result.calories == pytest.approx(570.0)
        assert mock_aclient.models.generate_content.call_count == 1

    async def test_first_call_is_plain_not_grounded(self):
        """First call must be plain (no google_search tool) — grounding is a fallback."""
        resp = _make_response([{"text": _VALID_JSON}])
        client_cls, mock_aclient = _make_mock_client([resp])

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            await gemini_analyze_meal([], user_text="salad")

        first_config = mock_aclient.models.generate_content.call_args_list[0][1]["config"]
        tools = first_config.tools or []
        assert all(getattr(t, "google_search", None) is None for t in tools), \
            "First call must NOT include google_search tool (plain-first ordering)"

    async def test_plain_non_json_prose_triggers_grounded_fallback(self):
        """When plain call returns conversational prose (not JSON), a grounded fallback fires."""
        prose_resp = _make_response(
            [{"text": "Pizza margherita has about 570 kcal per serving."}],
        )
        json_resp = _make_response([{"text": _VALID_JSON}], web_search_queries=["pizza margherita nutrition"])
        client_cls, mock_aclient = _make_mock_client([prose_resp, json_resp])

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            text, result = await gemini_analyze_meal([], user_text="pizza margherita")

        # Two calls fired: plain (failed parse) + grounded fallback
        assert mock_aclient.models.generate_content.call_count == 2
        # Second call MUST include google_search tool (grounded fallback)
        second_config = mock_aclient.models.generate_content.call_args_list[1][1]["config"]
        grounded_tools = second_config.tools or []
        assert any(getattr(t, "google_search", None) is not None for t in grounded_tools), \
            "Fallback call should include google_search tool"
        # Result still comes back (from the grounded call)
        assert result is not None
        assert result.item_name == "Pizza Margherita"

    async def test_plain_empty_text_triggers_grounded_fallback(self):
        """Plain call returns only function-call parts (empty text) → grounded fallback fires."""
        empty_resp = _make_response([{"function_call": True}])
        json_resp = _make_response([{"text": _VALID_JSON}], web_search_queries=["search query"])
        client_cls, mock_aclient = _make_mock_client([empty_resp, json_resp])

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            text, result = await gemini_analyze_meal([], user_text="pizza")

        assert mock_aclient.models.generate_content.call_count == 2
        assert result is not None

    async def test_image_only_uses_conversational_prompt(self):
        """Image-only call uses CONVERSATIONAL_INITIAL_PROMPT (not TEXT_ONLY)."""
        resp = _make_response([{"text": _VALID_JSON}])
        client_cls, mock_aclient = _make_mock_client([resp])
        fake_image = b"\xff\xd8\xff"  # minimal JPEG magic bytes

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            _, result = await gemini_analyze_meal([fake_image], user_text="")

        assert result is not None
        # Verify the content sent includes image inline_data
        call_kwargs = mock_aclient.models.generate_content.call_args_list[0][1]
        contents = call_kwargs["contents"]
        first_parts = contents[0].parts
        has_image_part = any(
            getattr(p, "inline_data", None) is not None for p in first_parts
        )
        assert has_image_part, "Image bytes must be sent as inline_data part"

    async def test_image_and_text_uses_combined_prompt(self):
        """Image + text note uses COMBINED_INITIAL_PROMPT (includes user note)."""
        resp = _make_response([{"text": _VALID_JSON}])
        client_cls, mock_aclient = _make_mock_client([resp])
        fake_image = b"\xff\xd8\xff"

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            _, result = await gemini_analyze_meal([fake_image], user_text="large portion")

        call_kwargs = mock_aclient.models.generate_content.call_args_list[0][1]
        contents = call_kwargs["contents"]
        # User note now lives in its own conversation turn (separate from the
        # instructions turn) as a prompt-injection hardening step. Scan all
        # turns' text parts for the note.
        all_text = [
            p.text
            for c in contents
            for p in c.parts
            if getattr(p, "text", None)
        ]
        assert any("large portion" in t for t in all_text), \
            "User note must be included somewhere in the sent content"

    async def test_both_calls_fail_returns_none_result(self):
        """Both grounded and plain calls return unparseable text → (text, None)."""
        bad1 = _make_response([{"text": "Sorry I cannot help."}])
        bad2 = _make_response([{"text": "Still cannot help."}])
        client_cls, mock_aclient = _make_mock_client([bad1, bad2])

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            text, result = await gemini_analyze_meal([], user_text="mystery meal")

        assert result is None

    async def test_exception_in_generate_returns_empty(self):
        """If the API call raises an exception, returns ("", None) cleanly."""
        client_cls, mock_aclient = _make_mock_client([RuntimeError("network error")])

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            text, result = await gemini_analyze_meal([], user_text="pizza")

        assert text == ""
        assert result is None


# ---------------------------------------------------------------------------
# gemini_eod_check - mocked Gemini client
# ---------------------------------------------------------------------------

def _make_plain_response(text):
    """Build a fake Gemini response returning plain text (no JSON needed)."""
    return _make_response([{"text": text}])


@pytest.mark.asyncio
class TestGeminiEodCheck:
    """Tests for gemini_eod_check - no real API calls."""

    _TARGET = {"calories": 2000.0, "protein": 150.0, "carbs": 200.0, "fat": 70.0}
    _TOTALS = {"calories": 1750.0, "protein": 130.0, "carbs": 180.0, "fat": 60.0, "meal_count": 3}
    _MEALS = [
        {"logged_at": "2026-03-01 08:00", "meal_type": "breakfast",
         "item_name": "Oatmeal", "meal_description": "with berries",
         "calories": 400, "protein": 12, "carbs": 70, "fat": 8},
        {"logged_at": "2026-03-01 13:00", "meal_type": "lunch",
         "item_name": "Chicken salad", "meal_description": "",
         "calories": 550, "protein": 45, "carbs": 30, "fat": 22},
        {"logged_at": "2026-03-01 20:30", "meal_type": "dinner",
         "item_name": "Salmon and rice", "meal_description": "",
         "calories": 800, "protein": 73, "carbs": 80, "fat": 30},
    ]

    async def test_no_api_key_returns_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GEMINI_API_KEY", None)
            result = await gemini_eod_check(self._TARGET, self._TOTALS, self._MEALS)
        assert result == ""

    async def test_returns_ai_text(self):
        ai_reply = "Great work today! You hit your protein goal. Tomorrow, aim for a bit more carbs."
        resp = _make_plain_response(ai_reply)
        client_cls, mock_aclient = _make_mock_client([resp])

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            result = await gemini_eod_check(self._TARGET, self._TOTALS, self._MEALS)

        assert result == ai_reply
        assert mock_aclient.models.generate_content.call_count == 1

    async def test_exception_returns_empty_string(self):
        client_cls, _ = _make_mock_client([RuntimeError("network error")])

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            result = await gemini_eod_check(self._TARGET, self._TOTALS, self._MEALS)

        assert result == ""

    async def test_empty_response_returns_empty_string(self):
        resp = _make_plain_response("")
        client_cls, _ = _make_mock_client([resp])

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            result = await gemini_eod_check(self._TARGET, self._TOTALS, self._MEALS)

        assert result == ""

    async def test_prompt_includes_macro_targets(self):
        """Targets are forwarded to the model so it can comment on progress."""
        resp = _make_plain_response("Great job!")
        client_cls, mock_aclient = _make_mock_client([resp])

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            await gemini_eod_check(self._TARGET, self._TOTALS, self._MEALS)

        call_kwargs = mock_aclient.models.generate_content.call_args_list[0][1]
        prompt_text = call_kwargs["contents"].parts[0].text
        assert "2000" in prompt_text   # calorie target
        assert "150" in prompt_text    # protein target

    async def test_prompt_includes_meal_types(self):
        """Meal categories (breakfast/lunch/dinner) appear in the prompt."""
        resp = _make_plain_response("Nice work!")
        client_cls, mock_aclient = _make_mock_client([resp])

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            await gemini_eod_check(self._TARGET, self._TOTALS, self._MEALS)

        prompt_text = mock_aclient.models.generate_content.call_args_list[0][1]["contents"].parts[0].text
        assert "Breakfast" in prompt_text
        assert "Lunch" in prompt_text
        assert "Dinner" in prompt_text

    async def test_prompt_includes_meal_names(self):
        resp = _make_plain_response("Nice work!")
        client_cls, mock_aclient = _make_mock_client([resp])

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            await gemini_eod_check(self._TARGET, self._TOTALS, self._MEALS)

        prompt_text = mock_aclient.models.generate_content.call_args_list[0][1]["contents"].parts[0].text
        assert "Oatmeal" in prompt_text
        assert "Chicken salad" in prompt_text
        assert "Salmon and rice" in prompt_text

    async def test_prompt_includes_weekly_avg_when_provided(self):
        weekly_avg = {"calories": 1800.0, "protein": 120.0, "carbs": 190.0, "fat": 65.0}
        resp = _make_plain_response("Good trend!")
        client_cls, mock_aclient = _make_mock_client([resp])

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            await gemini_eod_check(self._TARGET, self._TOTALS, self._MEALS, weekly_avg=weekly_avg)

        prompt_text = mock_aclient.models.generate_content.call_args_list[0][1]["contents"].parts[0].text
        assert "1800" in prompt_text   # weekly avg calories

    async def test_weekly_avg_data_absent_when_none(self):
        """When weekly_avg=None the data line (with numbers) is not in the prompt."""
        resp = _make_plain_response("Keep going!")
        client_cls, mock_aclient = _make_mock_client([resp])

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            await gemini_eod_check(self._TARGET, self._TOTALS, self._MEALS, weekly_avg=None)

        prompt_text = mock_aclient.models.generate_content.call_args_list[0][1]["contents"].parts[0].text
        # The data line is "7-day average: NNN kcal | ..." - check the pipe-separated format is absent
        assert "7-day average:" not in prompt_text

    async def test_user_name_included_when_provided(self):
        resp = _make_plain_response("Hey Alex!")
        client_cls, mock_aclient = _make_mock_client([resp])

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            await gemini_eod_check(
                self._TARGET, self._TOTALS, self._MEALS, user_name="Alex"
            )

        prompt_text = mock_aclient.models.generate_content.call_args_list[0][1]["contents"].parts[0].text
        assert "Alex" in prompt_text

    async def test_single_api_call_made(self):
        """EOD check is a single generate_content call (no grounding fallback)."""
        resp = _make_plain_response("You did great!")
        client_cls, mock_aclient = _make_mock_client([resp])

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            await gemini_eod_check(self._TARGET, self._TOTALS, self._MEALS)

        assert mock_aclient.models.generate_content.call_count == 1

    async def test_retries_on_transient_error(self):
        """gemini_eod_check retries up to 3 times on transient API errors."""
        ai_reply = "Great work today!"
        success_resp = _make_plain_response(ai_reply)
        # First two calls raise, third succeeds
        client_cls, mock_aclient = _make_mock_client([
            RuntimeError("503 Service Unavailable"),
            RuntimeError("503 Service Unavailable"),
            success_resp,
        ])

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await gemini_eod_check(self._TARGET, self._TOTALS, self._MEALS)

        assert result == ai_reply
        assert mock_aclient.models.generate_content.call_count == 3

    async def test_returns_empty_after_3_failed_retries(self):
        """Returns '' when all 3 retry attempts fail."""
        client_cls, mock_aclient = _make_mock_client([
            RuntimeError("err"),
            RuntimeError("err"),
            RuntimeError("err"),
        ])

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await gemini_eod_check(self._TARGET, self._TOTALS, self._MEALS)

        assert result == ""
        assert mock_aclient.models.generate_content.call_count == 3


# ---------------------------------------------------------------------------
# Questions field in _extract_json / _parse_nutrition_response
# ---------------------------------------------------------------------------

class TestQuestionsField:
    """The questions array from Gemini passes through parsing and is accessible."""

    def test_extract_json_preserves_questions(self):
        raw = json.dumps({
            "item_name": "Pasta",
            "meal_description": "Pasta dish",
            "items": [],
            "calories": 600,
            "protein": 20,
            "carbs": 90,
            "fat": 15,
            "questions": ["Was the sauce cream-based or tomato?", "How large was the serving?"],
        })
        result = _extract_json(raw)
        assert "questions" in result
        assert len(result["questions"]) == 2
        assert "cream-based" in result["questions"][0]

    def test_extract_json_questions_absent_is_missing_key(self):
        """When Gemini is confident, questions key is simply absent."""
        raw = json.dumps({
            "item_name": "Salad",
            "meal_description": "Green salad",
            "items": [],
            "calories": 150,
            "protein": 3,
            "carbs": 10,
            "fat": 8,
        })
        result = _extract_json(raw)
        assert result.get("questions") is None

    def test_extract_json_empty_questions_list(self):
        """Empty questions array is valid (no questions)."""
        raw = json.dumps({
            "item_name": "Rice",
            "meal_description": "White rice",
            "items": [],
            "calories": 200,
            "protein": 4,
            "carbs": 44,
            "fat": 0,
            "questions": [],
        })
        result = _extract_json(raw)
        assert result["questions"] == []


# ---------------------------------------------------------------------------
# gemini_suggest_targets — single-call structured-output path
# ---------------------------------------------------------------------------


def _make_client_cls_for_target_call(text: str):
    """Patch genai.Client so the single call returns a fake response with the given text."""
    resp = _make_response([{"text": text}])
    mock_aclient = AsyncMock()
    mock_aclient.models.generate_content = AsyncMock(return_value=resp)

    mock_aio = MagicMock()
    mock_aio.__aenter__ = AsyncMock(return_value=mock_aclient)
    mock_aio.__aexit__ = AsyncMock(return_value=False)

    mock_client = MagicMock()
    mock_client.aio = mock_aio
    return MagicMock(return_value=mock_client), mock_aclient


_INITIAL_TARGETS_PAYLOAD = {
    "reply_text": "Based on your stats, I'd recommend 2100 calories, 160g protein, 230g carbs, 65g fat.",
    "targets_changed": True,
    "calories": 2100,
    "protein": 160,
    "carbs": 230,
    "fat": 65,
    "explanation": "Maintains weight at your stated activity level.",
    "profile": {"age": 28, "sex": "male", "weight_kg": 82.0, "height_cm": 180.0},
}


@pytest.mark.asyncio
class TestGeminiSuggestTargets:
    """Single structured-output call: chat reply + optional target update."""

    async def test_targets_changed_true_populates_targets(self):
        """Initial suggest: model returns targets_changed=true with valid numbers."""
        client_cls, mock_aclient = _make_client_cls_for_target_call(
            json.dumps(_INITIAL_TARGETS_PAYLOAD)
        )
        conversation: list[dict] = []

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            reply, parsed = await gemini_suggest_targets(
                user_context="33yo male, 82kg, maintain weight",
                conversation=conversation,
            )

        assert mock_aclient.models.generate_content.call_count == 1, \
            "Single structured call - no second extraction"
        assert parsed is not None
        assert parsed["calories"] == 2100
        assert parsed["protein"] == 160
        assert "2100" in reply
        # The conversation must store reply_text only, never the JSON payload.
        assert conversation[-1]["role"] == "model"
        assert conversation[-1]["text"] == _INITIAL_TARGETS_PAYLOAD["reply_text"]
        assert "targets_changed" not in conversation[-1]["text"]

    async def test_marathon_refine_emits_new_targets(self):
        """Regression: 'I am preparing for a marathon' must produce new numbers, not a 'I'll recalculate' stall."""
        marathon_payload = {
            "reply_text": "Marathon training - I'd bump you to 3000 calories, 130g protein, 410g carbs, 80g fat.",
            "targets_changed": True,
            "calories": 3000,
            "protein": 130,
            "carbs": 410,
            "fat": 80,
            "explanation": "Higher carbs to fuel endurance training.",
            "profile": None,
        }
        client_cls, _ = _make_client_cls_for_target_call(json.dumps(marathon_payload))
        # Prior conversation: initial suggest already happened.
        conversation = [
            {"role": "user", "text": "My profile: age 33, male, 82 kg\n\nmaintain weight"},
            {"role": "model", "text": "I'd recommend 2800 calories, 158g protein, 320g carbs, 88g fat."},
        ]

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            reply, parsed = await gemini_suggest_targets(
                user_context="I am preparing for a marathon",
                conversation=conversation,
            )

        assert parsed is not None, "Marathon turn must update targets - this is the bug we're fixing"
        assert parsed["calories"] == 3000
        assert parsed["carbs"] == 410
        assert "marathon" in reply.lower()

    async def test_targets_changed_false_keeps_targets_null(self):
        """Informational question: model sets targets_changed=false, no update."""
        info_payload = {
            "reply_text": "Fiber is a non-digestible carbohydrate that supports gut health and satiety.",
            "targets_changed": False,
            "calories": None,
            "protein": None,
            "carbs": None,
            "fat": None,
            "explanation": None,
            "profile": None,
        }
        client_cls, _ = _make_client_cls_for_target_call(json.dumps(info_payload))
        conversation = [
            {"role": "user", "text": "intro"},
            {"role": "model", "text": "I'd recommend 2100 calories, 160g protein, 230g carbs, 65g fat."},
        ]

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            reply, parsed = await gemini_suggest_targets(
                user_context="what does fiber do?",
                conversation=conversation,
            )

        assert parsed is None, "Info-only turn must not overwrite targets"
        assert "fiber" in reply.lower()

    async def test_targets_changed_true_with_insane_numbers_rejected(self):
        """Hallucination protection: sanity bounds reject calories outside the 1200-6000 window."""
        bad_payload = {
            **_INITIAL_TARGETS_PAYLOAD,
            "calories": 99999,  # way outside _MAX_CALORIES
        }
        client_cls, _ = _make_client_cls_for_target_call(json.dumps(bad_payload))

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            reply, parsed = await gemini_suggest_targets(
                user_context="set my targets",
                conversation=[],
            )

        assert parsed is None, "Sanity bounds must reject hallucinated calories"
        # reply_text still surfaces so the user isn't stranded
        assert reply  # non-empty

    async def test_non_json_output_treated_as_no_update(self):
        """If the model violates response_schema and emits plain prose, treat as conversational with no target update."""
        client_cls, _ = _make_client_cls_for_target_call(
            "Sorry, I had trouble formatting that. Could you rephrase?"
        )
        conversation: list[dict] = []

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            reply, parsed = await gemini_suggest_targets(
                user_context="hi",
                conversation=conversation,
            )

        assert parsed is None
        assert "Sorry" in reply
        # The fallback still appends a model turn so the conversation stays well-formed.
        assert conversation[-1]["role"] == "model"

    async def test_call_uses_response_schema(self):
        """The single call must be configured with response_mime_type=application/json and a response_schema."""
        client_cls, mock_aclient = _make_client_cls_for_target_call(
            json.dumps(_INITIAL_TARGETS_PAYLOAD)
        )

        with patch("src.gemini.genai.Client", client_cls), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            await gemini_suggest_targets(user_context="hi", conversation=[])

        kwargs = mock_aclient.models.generate_content.call_args_list[0][1]
        config = kwargs["config"]
        assert getattr(config, "response_mime_type", None) == "application/json"
        assert getattr(config, "response_schema", None) is not None
