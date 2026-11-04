# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Email through SMTP the owner configures: the report as plain text, the PDF attached."""

from __future__ import annotations

import asyncio
import contextlib
import smtplib
import ssl
from collections.abc import Mapping
from email.message import EmailMessage
from typing import Any

from trading_r1.delivery.base import Channel, ChannelError, Field, Kind, Receipt
from trading_r1.delivery.message import Message
from trading_r1.state.runtime_config import runtime_config


class EmailChannel(Channel):
    """SMTP the owner configures: the report as plain text, the PDF attached. TLS is required
    and verified (`starttls` on a submission port, or `ssl` from the first byte)."""

    name = "email"

    def __init__(self, cfg: Mapping[str, Any], env: Mapping[str, str]) -> None:
        """``cfg`` is what ``KIND.validate`` returned."""
        self.host = cfg["host"]
        self.security = cfg.get("security", "starttls")
        self.port = cfg.get("port") or (465 if self.security == "ssl" else 587)
        self.to = list(cfg["to"])
        self.sender = cfg.get("from") or self.to[0]
        env_name = str(cfg.get("password_env") or "")
        self.password = env.get(env_name, "") if env_name else ""
        # a login is the sender's address unless another is given; no password, no login
        self.username = str(cfg.get("username") or (self.sender if self.password else ""))
        if self.username and not self.password:
            raise ChannelError(f"email: set {env_name or 'password_env'} (the SMTP password)")

    def address(self) -> str:
        return ", ".join(self.to)

    def _build(self, message: Message) -> EmailMessage:
        mail = EmailMessage()
        mail["Subject"] = " ".join(message.subject.split())  # one line
        mail["From"] = self.sender
        mail["To"] = ", ".join(self.to)
        mail.set_content(message.text)
        for a in message.sendable():
            maintype, _, subtype = a.content_type.partition("/")
            mail.add_attachment(a.data, maintype=maintype, subtype=subtype, filename=a.name)
        return mail

    def _send_sync(self, message: Message) -> Receipt:
        mail = self._build(message)
        context = ssl.create_default_context()
        server: smtplib.SMTP | None = None
        handed_over = False  # from here on the server may have taken the message
        try:
            if self.security == "ssl":
                server = smtplib.SMTP_SSL(
                    self.host,
                    self.port,
                    timeout=runtime_config().delivery.send_timeout_s,
                    context=context,
                )
            else:
                server = smtplib.SMTP(
                    self.host, self.port, timeout=runtime_config().delivery.send_timeout_s
                )
                server.starttls(context=context)
            if self.username:
                server.login(self.username, self.password)
            handed_over = True
            refused = server.send_message(mail)
        except (
            smtplib.SMTPRecipientsRefused,
            smtplib.SMTPSenderRefused,
            smtplib.SMTPDataError,
        ) as err:
            return Receipt("failed", f"the server refused it: {err}"[:300])
        except (OSError, smtplib.SMTPException) as err:
            if handed_over:  # a timeout, a dropped connection or a TLS error once it was sent
                return Receipt("unknown", f"{type(err).__name__} after the message was sent")
            return Receipt("failed", f"{type(err).__name__}: {err}"[:300])
        finally:
            if server is not None:
                with contextlib.suppress(Exception):  # closing says nothing about the send
                    server.quit()
        if refused:
            return Receipt("unknown", f"accepted for some recipients only: {sorted(refused)}")
        return Receipt("sent", "accepted by the mail server")

    async def send(self, message: Message) -> Receipt:
        return await asyncio.to_thread(self._send_sync, message)


def _check(values: dict[str, Any]) -> list[str]:
    problems = []
    if values.get("username") and not values.get("password_env"):
        problems.append("password_env: required with a username")
    return problems


KIND = Kind(
    name="email",
    label="Email",
    summary="Reports to your own mailbox through your provider's SMTP server.",
    fields=(
        Field("host", "SMTP server", "text", required=True, example="smtp.gmail.com"),
        Field(
            "security",
            "Connection",
            "choice",
            default="starttls",
            choices=("starttls", "ssl"),
            help="starttls is port 587, ssl is port 465. The certificate is always verified.",
        ),
        Field(
            "port",
            "Port",
            "int",
            minimum=1,
            maximum=65535,
            help="Left empty: 587 for starttls, 465 for ssl.",
        ),
        Field(
            "username",
            "Username",
            "text",
            help="Empty: the From address. Set it when the provider logs in with something else.",
        ),
        Field(
            "password_env",
            "Password",
            "secret",
            default="TR1_SMTP_PASSWORD",
            help="The SMTP password. Gmail, iCloud, Yahoo and Fastmail need an app password, "
            "made in the account's security settings.",
        ),
        Field(
            "to", "To", "list", required=True, help="Your own address.", example="me@example.com"
        ),
        Field("from", "From", "text", help="Empty: the first To address."),
    ),
    make=EmailChannel,
    presets={
        "gmail": {"host": "smtp.gmail.com", "security": "starttls"},
        "icloud": {"host": "smtp.mail.me.com", "security": "starttls"},
        "yahoo": {"host": "smtp.mail.yahoo.com", "security": "ssl"},
        "fastmail": {"host": "smtp.fastmail.com", "security": "ssl"},
    },
    check=_check,
)
