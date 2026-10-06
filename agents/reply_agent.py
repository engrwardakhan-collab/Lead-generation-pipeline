from __future__ import annotations

import dataclasses
import email as email_lib
import imaplib
import re
from typing import Optional

from openai import APIError, APITimeoutError, OpenAI, RateLimitError as OpenAIRateLimitError

from config.settings import Settings, get_settings
from core.database import ContactRepository
from core.exceptions import ConfigurationError, EnrichmentError, RateLimitError
from core.logging_config import get_logger

_VALID_CLASSIFICATIONS: frozenset[str] = frozenset({
    "interested",
    "not_interested",
    "unsubscribe",
})

_SYSTEM_PROMPT = (
    "Classify the following reply to a cold outreach email as exactly one of:\n"
    "- interested   (wants to learn more, asks a question, or agrees to a call)\n"
    "- not_interested (declines, says no, or is not relevant)\n"
    "- unsubscribe  (asks to be removed, stop emailing, or opt out)\n"
    "Output only the single classification word. Nothing else."
)

_EMAIL_RE = re.compile(r"[\w._%+\-]+@[\w.\-]+\.[a-zA-Z]{2,}")

_MAX_BODY_CHARS = 1_000  # chars sent to GPT - enough to classify, cheap to token-count


@dataclasses.dataclass
class ReplyResult:
    processed: int = 0
    interested: int = 0
    not_interested: int = 0
    unsubscribed: int = 0
    no_match: int = 0
    errors: int = 0


class ReplyAgent:
    """
    Polls the IMAP inbox for unread replies, classifies each with GPT-4o-mini,
    and updates Supabase status accordingly.
    Only processes replies from leads currently at status='contacted'.
    """

    def __init__(
        self,
        contacts: ContactRepository,
        settings: Optional[Settings] = None,
    ) -> None:
        self._contacts = contacts
        self._s = settings or get_settings()
        self.logger = get_logger("agents.reply_agent")
        if not self._s.imap_server:
            raise ConfigurationError("IMAP_SERVER is required for ReplyAgent")
        if not self._s.imap_username:
            raise ConfigurationError("IMAP_USERNAME is required for ReplyAgent")
        if not self._s.imap_password:
            raise ConfigurationError("IMAP_PASSWORD is required for ReplyAgent")
        if not self._s.openai_api_key:
            raise ConfigurationError("OPENAI_API_KEY is required for ReplyAgent")
        self._gpt = OpenAI(api_key=self._s.openai_api_key)

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self, max_messages: int = 0) -> ReplyResult:
        """Check inbox for unread replies and classify each one."""
        result = ReplyResult()
        self.logger.info("ReplyAgent START - max_messages=%s", max_messages or "unlimited")

        try:
            with self._connect() as imap:
                msg_ids = self._fetch_unseen_ids(imap)
                if not msg_ids:
                    self.logger.info("No unread messages found")
                    return result

                if max_messages:
                    msg_ids = msg_ids[:max_messages]

                self.logger.info("Processing %d unread message(s)", len(msg_ids))

                for msg_id in msg_ids:
                    self._process_one(imap, msg_id, result)

        except imaplib.IMAP4.error as exc:
            self.logger.error("IMAP connection failed: %s", exc)
            raise ConfigurationError(f"IMAP error: {exc}") from exc

        self.logger.info(
            "ReplyAgent DONE - processed=%d interested=%d not_interested=%d "
            "unsubscribed=%d no_match=%d errors=%d",
            result.processed, result.interested, result.not_interested,
            result.unsubscribed, result.no_match, result.errors,
        )
        return result

    # ── IMAP helpers ──────────────────────────────────────────────────────────

    def _connect(self) -> imaplib.IMAP4_SSL:
        imap = imaplib.IMAP4_SSL(self._s.imap_server, self._s.imap_port)
        imap.login(self._s.imap_username, self._s.imap_password)
        imap.select("INBOX")
        return imap

    def _fetch_unseen_ids(self, imap: imaplib.IMAP4_SSL) -> list[bytes]:
        _, data = imap.search(None, "UNSEEN")
        ids = data[0].split() if data[0] else []
        return ids

    def _process_one(
        self, imap: imaplib.IMAP4_SSL, msg_id: bytes, result: ReplyResult
    ) -> None:
        try:
            _, raw = imap.fetch(msg_id, "(RFC822)")
            msg = email_lib.message_from_bytes(raw[0][1])

            from_email = self._parse_from_email(msg.get("From", ""))
            if not from_email:
                self.logger.debug("No From address in message %s - skipping", msg_id)
                imap.store(msg_id, "+FLAGS", "\\Seen")
                result.no_match += 1
                return

            lead = self._contacts.get_contacted_by_email(from_email)
            if not lead:
                self.logger.debug("No matching contacted contact for %s", from_email)
                imap.store(msg_id, "+FLAGS", "\\Seen")
                result.no_match += 1
                return

            body = self._extract_body(msg)
            reply_text = self._strip_quoted(body)
            if not reply_text.strip():
                self.logger.debug("Empty reply body from %s - skipping", from_email)
                imap.store(msg_id, "+FLAGS", "\\Seen")
                result.no_match += 1
                return

            classification = self._classify(reply_text)
            if not classification:
                self.logger.warning("GPT gave invalid classification for %s", lead["name"])
                result.errors += 1
                imap.store(msg_id, "+FLAGS", "\\Seen")
                return

            self._contacts.update_reply(lead["id"], classification)
            imap.store(msg_id, "+FLAGS", "\\Seen")
            result.processed += 1

            self.logger.info(
                "Reply classified | name=%s | classification=%s",
                lead["name"], classification,
            )

            if classification == "interested":
                result.interested += 1
            elif classification == "not_interested":
                result.not_interested += 1
            else:
                result.unsubscribed += 1

        except RateLimitError:
            self.logger.error("OpenAI rate limit reached - stopping reply run")
            raise
        except Exception as exc:
            self.logger.error("Error processing message %s: %s", msg_id, exc)
            result.errors += 1

    # ── Email parsing ─────────────────────────────────────────────────────────

    def _parse_from_email(self, from_header: str) -> Optional[str]:
        m = _EMAIL_RE.search(from_header)
        return m.group(0).lower() if m else None

    def _extract_body(self, msg: email_lib.message.Message) -> str:
        """Return plain text body, falling back to stripped HTML if no text/plain part."""
        plain: Optional[str] = None
        html: Optional[str] = None

        parts = msg.walk() if msg.is_multipart() else [msg]
        for part in parts:
            ct = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                text = payload.decode(charset, errors="replace")
                if ct == "text/plain" and plain is None:
                    plain = text
                elif ct == "text/html" and html is None:
                    html = re.sub(r"<[^>]+>", " ", text)
                    html = re.sub(r"\s+", " ", html).strip()
            except Exception as exc:
                self.logger.debug("Part decode failed (%s): %s", ct, exc)

        return plain or html or ""

    @staticmethod
    def _strip_quoted(body: str) -> str:
        """Remove quoted reply lines (starting with '>') and trim to _MAX_BODY_CHARS."""
        lines = [
            line for line in body.splitlines()
            if line.strip() and not line.strip().startswith(">")
        ]
        return " ".join(lines)[:_MAX_BODY_CHARS]

    # ── Classification ────────────────────────────────────────────────────────

    def _classify(self, text: str) -> Optional[str]:
        """Ask GPT-4o-mini to classify the reply. Returns None if response is invalid."""
        try:
            resp = self._gpt.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                max_tokens=10,
                temperature=0,
            )
        except OpenAIRateLimitError as exc:
            raise RateLimitError(f"OpenAI rate limit: {exc}") from exc
        except (APIError, APITimeoutError) as exc:
            raise EnrichmentError(f"OpenAI API error: {exc}") from exc

        raw = (resp.choices[0].message.content or "").strip().lower()
        return raw if raw in _VALID_CLASSIFICATIONS else None
