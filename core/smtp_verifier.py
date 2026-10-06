from __future__ import annotations

import smtplib
import socket
from typing import Optional

import dns.resolver
from dns.exception import DNSException

from core.logging_config import get_logger

logger = get_logger("smtp_verifier")

_HELO_DOMAIN = "convertwithai.tech"
_MAIL_FROM = f"verify@{_HELO_DOMAIN}"
_TIMEOUT = 10  # seconds
_SMTP_PORT = 25  # standard MX-to-MX transfer port


class SmtpVerifier:
    """
    Confirms an email address is likely deliverable without sending a message.
    Step 1: DNS MX lookup — if the domain has no mail server, reject immediately.
    Step 2: SMTP EHLO + RCPT TO on port 25. A 250 response means the server
            accepts the address. An explicit 550 means it doesn't exist.
    If port 25 is blocked (residential ISPs, some cloud hosts), we give the
    address the benefit of the doubt and return True.
    """

    def verify(self, email: str) -> bool:
        """Return True if the address appears deliverable."""
        try:
            domain = email.split("@", 1)[1]
        except IndexError:
            return False

        mx = self._get_mx(domain)
        if not mx:
            return False  # No MX record = domain cannot receive mail

        return self._rcpt_check(mx, email)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_mx(self, domain: str) -> Optional[str]:
        try:
            answers = sorted(
                dns.resolver.resolve(domain, "MX"),
                key=lambda r: r.preference,
            )
            return str(answers[0].exchange).rstrip(".")
        except DNSException as exc:
            logger.debug("MX lookup failed for %s: %s", domain, exc)
            return None

    def _rcpt_check(self, mx_host: str, email: str) -> bool:
        try:
            with smtplib.SMTP(timeout=_TIMEOUT) as smtp:
                smtp.connect(mx_host, _SMTP_PORT)
                smtp.ehlo(_HELO_DOMAIN)
                smtp.mail(_MAIL_FROM)
                code, _ = smtp.rcpt(email)
                return code == 250
        except smtplib.SMTPRecipientsRefused:
            return False  # Server explicitly rejected this address
        except (ConnectionRefusedError, TimeoutError, socket.gaierror, OSError):
            # Port 25 blocked or host unreachable — cannot verify, assume valid
            logger.debug("Port 25 unreachable for %s, skipping SMTP probe", mx_host)
            return True
        except smtplib.SMTPException as exc:
            logger.debug("SMTP error for %s via %s: %s", email, mx_host, exc)
            return True  # Unknown SMTP state — assume valid rather than discard
