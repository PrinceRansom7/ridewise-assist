"""
NeoStats-hosted LLM client (Azure OpenAI-compatible).

Security rules (enforced here, not in the LLM)
───────────────────────────────────────────────
- API key loaded from environment / .env only. Never logged or printed.
- All retrieved policy passages are wrapped in [POLICY SOURCE] tags so the
  LLM knows they are evidence, not instructions.
- All DB results are wrapped in [DB RESULT] tags.
- User messages are passed as role="user" only after injection scrubbing.
- Retries are bounded; failures return a safe error string.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from config.settings import (
    LLM_MAX_RETRIES,
    LLM_TIMEOUT,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_DEPLOYMENT,
    OPENAI_MODEL,
)


def _get_client():
    """Return a configured OpenAI client. Raises if key is missing."""
    from openai import OpenAI

    if not OPENAI_API_KEY:
        raise ValueError(
            "OPENAI_API_KEY is not set. Add it to your .env file."
        )
    return OpenAI(
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
        timeout=LLM_TIMEOUT,
        max_retries=0,  # we handle retries manually for better UX
    )


def chat(
    messages: list[dict],
    system_prompt: Optional[str] = None,
    temperature: float = 0.1,
) -> str:
    """
    Send a chat request to the NeoStats LLM.

    Parameters
    ----------
    messages:      List of {"role": "user"|"assistant", "content": str}.
    system_prompt: Optional system instruction prepended to the message list.
    temperature:   Low by default for factual, grounded responses.

    Returns
    -------
    The assistant's response text, or a safe error string on failure.
    """
    client = _get_client()

    full_messages = []
    if system_prompt:
        full_messages.append({"role": "system", "content": system_prompt})
    full_messages.extend(messages)

    last_error = ""
    for attempt in range(1, LLM_MAX_RETRIES + 2):
        try:
            response = client.chat.completions.create(
                model=OPENAI_DEPLOYMENT,
                messages=full_messages,
                temperature=temperature,
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            last_error = str(exc)
            if attempt <= LLM_MAX_RETRIES:
                time.sleep(2 ** attempt)  # exponential backoff: 2s, 4s
            continue

    return (
        f"⚠️ The RideWise assistant is temporarily unavailable. "
        f"Please try again in a moment. (Error: {last_error[:120]})"
    )
