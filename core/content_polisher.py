from __future__ import annotations

import json
import re
from typing import Optional

from openai import APIError, APITimeoutError, OpenAI, RateLimitError as OpenAIRateLimitError

from config.settings import Settings, get_settings
from core.exceptions import ConfigurationError, EnrichmentError, RateLimitError
from core.logging_config import get_logger

logger = get_logger("core.content_polisher")

_SYSTEM_PROMPT = (
    "You refine a user's own draft cold-outreach email. "
    "Keep their meaning, structure, and key points intact - do not add new claims "
    "or change the offer. Only tighten the wording into a more formal, professional "
    "tone and fix any awkward phrasing or grammar.\n"
    'Return ONLY a JSON object with two keys: "subject" and "body".'
)

_USER_TEMPLATE = """\
Subject: {subject}

Body:
{body}

Rewrite the above in a more formal, professional tone."""

_MAX_TOKENS = 400


class ContentPolisher:
    """
    Takes a user's manually-edited email draft and asks GPT-4o-mini to tighten
    it into a more formal/professional tone, without regenerating from scratch.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._s = settings or get_settings()
        if not self._s.openai_api_key:
            raise ConfigurationError("OPENAI_API_KEY is required for ContentPolisher")
        self._gpt = OpenAI(api_key=self._s.openai_api_key)

    def polish(self, subject: str, body: str) -> tuple[str, str]:
        """Return (subject, body) refined for tone. Falls back to the original text
        if GPT's response can't be parsed, rather than losing the user's edit."""
        prompt = _USER_TEMPLATE.format(subject=subject, body=body)
        try:
            resp = self._gpt.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=_MAX_TOKENS,
                temperature=0.3,
            )
        except OpenAIRateLimitError as exc:
            raise RateLimitError(f"OpenAI rate limit: {exc}") from exc
        except (APIError, APITimeoutError) as exc:
            raise EnrichmentError(f"OpenAI API error: {exc}") from exc

        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        try:
            data = json.loads(raw)
            new_subject = str(data.get("subject", "")).strip()
            new_body = str(data.get("body", "")).strip()
        except (json.JSONDecodeError, AttributeError):
            logger.warning("Polish response was not valid JSON — keeping original text")
            return subject, body

        if not new_subject or not new_body:
            return subject, body
        return new_subject, new_body
