from __future__ import annotations

import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Optional

from config.settings import Settings, get_settings
from core.exceptions import ConfigurationError
from core.logging_config import get_logger

logger = get_logger("core.brevo_sender")

_SMTP_TIMEOUT = 30  # seconds


class BrevoSender:
    """
    Sends plain-text emails via Brevo SMTP (smtp-relay.brevo.com:587, STARTTLS).
    Plain text only — higher deliverability, avoids spam filters.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._s = settings or get_settings()
        if not self._s.brevo_smtp_login:
            raise ConfigurationError("BREVO_SMTP_LOGIN is required for BrevoSender")
        if not self._s.brevo_smtp_key:
            raise ConfigurationError("BREVO_SMTP_KEY is required for BrevoSender")
        if not self._s.brevo_sender_email:
            raise ConfigurationError("BREVO_SENDER_EMAIL is required for BrevoSender")

    def send(self, to_email: str, to_name: str, subject: str, body: str) -> bool:
        """
        Send a plain-text email. Returns True on success, False on transient failure.
        Raises ConfigurationError if authentication fails (bad credentials — stop immediately).
        """
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = formataddr((self._s.brevo_sender_name, self._s.brevo_sender_email))
        msg["To"] = formataddr((to_name, to_email))

        try:
            with smtplib.SMTP(
                self._s.brevo_smtp_server,
                self._s.brevo_smtp_port,
                timeout=_SMTP_TIMEOUT,
            ) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(self._s.brevo_smtp_login, self._s.brevo_smtp_key)
                smtp.sendmail(self._s.brevo_sender_email, to_email, msg.as_string())
            logger.debug("Sent | to=%s | subject=%r", to_email, subject)
            return True
        except smtplib.SMTPAuthenticationError as exc:
            # Bad credentials → config error, no point retrying
            logger.error("Brevo authentication failed — check BREVO_SMTP_KEY: %s", exc)
            raise ConfigurationError(f"Brevo authentication failed: {exc}") from exc
        except (smtplib.SMTPException, OSError) as exc:
            logger.warning("Send failed | to=%s | error=%s", to_email, exc)
            return False
