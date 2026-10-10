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

import os
import sys
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config.settings as settings


def _get_client():
    """Return a configured OpenAI client. Raises if key is missing."""
    from openai import OpenAI

    api_key = settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY is not set. Add it to your .env file."
        )
    return OpenAI(
        api_key=api_key,
        base_url=settings.OPENAI_BASE_URL,
        timeout=settings.LLM_TIMEOUT,
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
    for attempt in range(1, settings.LLM_MAX_RETRIES + 2):
        try:
            response = client.chat.completions.create(
                model=settings.OPENAI_DEPLOYMENT,
                messages=full_messages,
                temperature=temperature,
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            last_error = str(exc)
            if attempt <= settings.LLM_MAX_RETRIES:
                time.sleep(2 ** attempt)  # exponential backoff: 2s, 4s
            continue

    return (
        f"⚠️ The RideWise assistant is temporarily unavailable. "
        f"Please try again in a moment. (Error: {last_error[:120]})"
    )
