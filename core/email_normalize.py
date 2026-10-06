"""Email canonicalization for duplicate detection.

The canonical form is used ONLY to decide "is this the same mailbox as one we
already have" (compared via a UNIQUE constraint on contacts.email_canonical).
It is never used for sending — the original, as-entered address is what gets
emailed.
"""
from __future__ import annotations

from typing import Optional

_GMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})


def canonicalize_email(email: str) -> Optional[str]:
    """Normalize an email address so equivalent mailboxes compare equal.

    Rules:
      - lowercase + trim
      - strip a '+tag' suffix from the local part (RFC 5233 subaddressing,
        supported by virtually every major provider)
      - additionally strip dots from the local part on gmail.com/googlemail.com,
        since Gmail ignores them but other providers treat dots as significant
      - googlemail.com is folded into gmail.com (same mailbox, Google's own alias)

    Returns None if `email` isn't in a `local@domain` shape.
    """
    local, sep, domain = email.strip().lower().partition("@")
    if not sep or not local or not domain:
        return None

    local = local.split("+", 1)[0]
    if domain in _GMAIL_DOMAINS:
        domain = "gmail.com"
        local = local.replace(".", "")

    if not local:
        return None

    return f"{local}@{domain}"
