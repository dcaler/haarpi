"""rabbitHole's notify binding — the shared mailer, fed from GlobalConfig.

Recipient is gc.notify_recipient (RABBITHOLE_NOTIFY_TO or contact_email);
mailer resolution and sending live in haarpi.notify.
"""

from __future__ import annotations

from haarpi import notify as _core

from .config import GlobalConfig


def send_email(subject: str, body: str, gc: GlobalConfig) -> bool:
    if not gc.notify_recipient:
        print("  [notify] no recipient set (RABBITHOLE_NOTIFY_TO or contact_email) "
              "— skipping email.")
        return False
    return _core.send_email(subject, body, to=gc.notify_recipient,
                            mail_prog=gc.mail_prog)
