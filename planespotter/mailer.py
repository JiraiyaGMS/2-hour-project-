"""Sends email over SMTP, or saves it to outbox/ when no login is configured."""

from __future__ import annotations

import logging
import re
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from .config import Config
from .emails import Email

log = logging.getLogger(__name__)


class Mailer:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def send(self, email: Email) -> Path | None:
        """Send ``email``. In dry-run mode returns the path it was saved to."""
        if not self.cfg.emails:
            return None  # emails are off (EMAILS=off): phone notifications only, nothing sent or saved
        if self.cfg.dry_run:
            return self._save(email)

        msg = EmailMessage()
        msg["Subject"] = email.subject
        msg["From"] = self.cfg.email_from or self.cfg.smtp_user
        msg["To"] = self.cfg.email_to
        msg.set_content(email.text)
        msg.add_alternative(email.html, subtype="html")
        for filename, data, mime in email.attachments:
            maintype, subtype = mime.split("/", 1)
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)

        context = ssl.create_default_context()
        if self.cfg.smtp_port == 465:
            with smtplib.SMTP_SSL(self.cfg.smtp_host, self.cfg.smtp_port, context=context, timeout=30) as smtp:
                smtp.login(self.cfg.smtp_user, self.cfg.smtp_password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port, timeout=30) as smtp:
                smtp.starttls(context=context)
                smtp.login(self.cfg.smtp_user, self.cfg.smtp_password)
                smtp.send_message(msg)
        log.info("emailed: %s", email.subject)
        return None

    def _save(self, email: Email) -> Path:
        outbox = Path(self.cfg.outbox_dir)
        outbox.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", email.subject.lower()).strip("-")[:60]
        path = outbox / f"{datetime.now():%Y%m%d-%H%M%S}-{slug}.html"
        path.write_text(email.html, encoding="utf-8")
        for filename, data, _ in email.attachments:
            (outbox / f"{path.stem}-{filename}").write_bytes(data)
        log.info("no email login set, saved instead: %s  (%s)", path, email.subject)
        return path
