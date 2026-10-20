"""Trigger the existing configured docassemble error email from the executor.

The independent executor must not import the application runtime, so this reads
the same ``error notification email`` and ``mail`` settings from ``config.yml``
and submits a plain message over the configured default SMTP transport
(docassemble's default is local Exim on ``localhost:25``). It reuses the existing
configured recipients/transport rather than adding a new notification system.

When no address is configured, or the transport is unavailable, it returns
honest operator guidance and never reports a fictitious recovery success.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

from yaml import YAMLError

from .config import load_config
from .paths import ExecutorPaths


def configured_recipients(config: dict[str, Any]) -> list[str]:
    value = config.get("error notification email")
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []


def _default_mail(config: dict[str, Any]) -> dict[str, Any]:
    mail = config.get("mail")
    if isinstance(mail, dict):
        return mail
    entries = [entry for entry in mail if isinstance(entry, dict)] if isinstance(mail, list) else []
    default = next((entry for entry in entries if entry.get("name") == "default"), None)
    return default or (entries[0] if entries else {})


def notify(paths: ExecutorPaths, *, subject: str, body: str, smtp_factory=None) -> dict[str, Any]:
    """Send one message over the configured transport, or return guidance.

    ``smtp_factory`` is an explicit injection seam for a disposable proof; the
    default uses the existing configured local mail transport.
    """
    try:
        config = load_config(paths.config_file)
    except (OSError, ValueError, YAMLError):
        return {"sent": False, "reason": "configuration-unreadable",
                "guidance": "The error email could not be addressed; configuration is not readable."}
    recipients = configured_recipients(config)
    if not recipients:
        return {"sent": False, "reason": "no-error-notification-email",
                "guidance": 'No "error notification email" is configured; use the executor and web logs.'}
    mail = _default_mail(config)
    server = mail.get("server") or "localhost"
    port = mail.get("port") or 25
    sender = (mail.get("default sender") or config.get("default sender") or
              "docassemble@localhost")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = str(sender)
    message["To"] = ", ".join(recipients)
    message.set_content(body)
    implicit_tls = mail.get("use ssl", False)
    try:
        if smtp_factory:
            options = {"context": ssl.create_default_context()} if implicit_tls else {}
            transport = smtp_factory(server, int(port), timeout=10, **options)
        elif implicit_tls:
            transport = smtplib.SMTP_SSL(server, int(port), timeout=10, context=ssl.create_default_context())
        else:
            transport = smtplib.SMTP(server, int(port), timeout=10)
        with transport as client:
            if not implicit_tls and mail.get("use tls", True):
                client.starttls(context=ssl.create_default_context())
            if mail.get("username"):
                client.login(mail["username"], mail.get("password") or "")
            client.send_message(message)
    except Exception:
        return {"sent": False, "reason": "email-unavailable",
                "guidance": "The configured error email could not be sent; inspect the executor and mail logs."}
    return {"sent": True, "reason": None, "recipients": recipients}
