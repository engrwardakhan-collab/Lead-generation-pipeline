from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "sent_emails.jsonl"


class SentEmailLog:
    """
    Permanent, append-only record of every email actually sent — one JSON line
    per send, capturing the exact rendered subject/body at send time.

    Independent of the email templates: editing agents/email_agent.py later
    never changes what's recorded here for past sends.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or _DEFAULT_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        *,
        contact_id: str,
        name: str,
        email: str,
        day: int,
        subject: str,
        body: str,
    ) -> None:
        """Append one sent-email record. Never raises — a logging failure must not break a send."""
        record = {
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "contact_id": contact_id,
            "name": name,
            "email": email,
            "day": day,
            "subject": subject,
            "body": body,
        }
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass  # best-effort — the DB status/timestamp remains the source of truth

    def recent(self, limit: int = 50) -> list[dict]:
        """Return the most recently sent emails, newest first."""
        if not self._path.exists():
            return []
        records = []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        except OSError:
            return []
        records.reverse()
        return records[:limit]

    def for_contact(self, contact_id: str) -> list[dict]:
        """Return all logged sends for one contact, oldest first. Used to give follow-up
        generation the original Day-1 content as context."""
        if not self._path.exists():
            return []
        records = []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    if record.get("contact_id") == contact_id:
                        records.append(record)
        except OSError:
            return []
        return records
