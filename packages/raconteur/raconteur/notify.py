"""raconteur's notify binding — the shared mailer, fed from GlobalConfig."""

from __future__ import annotations

from haarpi import notify as _core

from .config import GlobalConfig


def send_email(subject: str, body: str, gc: GlobalConfig) -> bool:
    if not gc.notify_recipient:
        return False
    return _core.send_email(subject, body, to=gc.notify_recipient,
                            mail_prog=gc.mail_prog)
