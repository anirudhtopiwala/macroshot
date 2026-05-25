"""Thin Gemini client that matches Macroshot's production invocation.

Differences from prod that we intentionally keep for eval cleanliness:
  - max_output_tokens=8192 (prod uses 3072 + retry loop on truncation).
  - response_mime_type="application/json" (prod uses no constraint + a
    3-tier retry loop on malformed JSON).

Differences we DO replicate from prod:
  - temperature=0.1
  - The "system" prompt is sent as a role=user turn (not as
    system_instruction) — prod does this for prompt-injection hardening
    and we mirror so token paths and model behavior match.
  - Image is attached to the same first user turn as the instructional text.
  - Image downscaled to max edge 1024 px before send.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from google import genai
from google.genai import types

# Reuse Macroshot's downscaler verbatim so behavior is identical.
_MACROSHOT_ROOT = Path(__file__).resolve().parents[3]
if str(_MACROSHOT_ROOT) not in sys.path:
    sys.path.insert(0, str(_MACROSHOT_ROOT))
from src.gemini import _downscale_image_for_gemini  # noqa: E402


_IMAGE_MIME = "image/jpeg"


@dataclass
class ModelCall:
    text: str
    latency_s: float
    input_tokens: int | None
    output_tokens: int | None    # visible output (candidates_token_count)
    thinking_tokens: int | None  # internal reasoning, billed at output rate
    total_tokens: int | None


def get_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    return genai.Client(api_key=api_key)


def call(
    *,
    model: str,
    system: str | None,
    user: str | None,
    image_path: str | None,
    temperature: float = 0.1,
    max_output_tokens: int = 8192,
) -> ModelCall:
    """Single Gemini call. Matches Macroshot prod (temp=0.1, prompt as user
    turn, image downscaled, image+prompt in same first user turn).

    Contents structure:
      [
        Content(role="user", parts=[
          Part(image_bytes)?,           # if image_path
          Part(text=system_prompt),     # if system (delivered as user!)
        ]),
        Content(role="user", parts=[
          Part(text=user_msg),          # if user (separate turn — matches prod)
        ]),
      ]
    """
    client = get_client()

    if not image_path and not system and not user:
        raise ValueError("call() requires at least one of system, user, image_path")

    # First user turn: image bytes (downscaled) + the instructional/system text.
    first_parts: list[types.Part] = []
    if image_path:
        raw = Path(image_path).read_bytes()
        downscaled = _downscale_image_for_gemini(raw)
        first_parts.append(types.Part(
            inline_data=types.Blob(data=downscaled, mime_type=_IMAGE_MIME)
        ))
    if system:
        first_parts.append(types.Part(text=system))

    contents: list[types.Content] = []
    if first_parts:
        contents.append(types.Content(role="user", parts=first_parts))
    if user:
        # Prod labels the user note (cf. src/gemini.py:1025). We mirror the
        # exact prefix so the model sees the same surface form.
        label = "User's note" if image_path else "User's description"
        # If there's no system prompt, the user msg can be the only/first turn.
        prefixed = f"{label}: {user}" if system else user
        contents.append(types.Content(role="user", parts=[types.Part(text=prefixed)]))

    config = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        response_mime_type="application/json",
        # NOTE: prod does NOT pass system_instruction — see module docstring.
    )

    t0 = time.perf_counter()
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=config,
    )
    elapsed = time.perf_counter() - t0

    usage = getattr(response, "usage_metadata", None)
    return ModelCall(
        text=response.text or "",
        latency_s=elapsed,
        input_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
        output_tokens=getattr(usage, "candidates_token_count", None) if usage else None,
        thinking_tokens=getattr(usage, "thoughts_token_count", None) if usage else None,
        total_tokens=getattr(usage, "total_token_count", None) if usage else None,
    )
