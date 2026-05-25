"""Optional email notifications via the local mail program.

Piggybacks on whatever mailer the server already uses for SLURM job mail
(SLURM's `MailProg`), so rabbitHole needs no SMTP credentials of its own. Resolution order
for the mail command:

  1. gc.mail_prog  (config [notify] mail_prog / RABBITHOLE_MAIL_PROG)
  2. SLURM's MailProg, read from `scontrol show config`
  3. the first of `mail`, `mailx`, `sendmail` found on PATH

Recipient is gc.notify_to (or contact_email). If no recipient or no mailer is
found, send_email() is a no-op (prints a hint) and never raises — notifications
must not break the pipeline.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import GlobalConfig


def _slurm_mailprog() -> str:
    try:
        out = subprocess.run(["scontrol", "show", "config"],
                             capture_output=True, text=True, timeout=10)
    except Exception:  # noqa: BLE001
        return ""
    for line in out.stdout.splitlines():
        if line.strip().startswith("MailProg"):
            val = line.split("=", 1)[1].strip() if "=" in line else ""
            if val and val.lower() != "(null)" and Path(val).exists():
                return val
    return ""


def _resolve_mailer(gc: GlobalConfig) -> str:
    if gc.mail_prog:
        return gc.mail_prog
    slurm = _slurm_mailprog()
    if slurm:
        return slurm
    for cand in ("mail", "mailx", "sendmail"):
        found = shutil.which(cand)
        if found:
            return found
    return ""


def send_email(subject: str, body: str, gc: GlobalConfig) -> bool:
    to_addr = gc.notify_recipient
    if not to_addr:
        print("  [notify] no recipient set (RABBITHOLE_NOTIFY_TO or contact_email) "
              "— skipping email.")
        return False

    mailer = _resolve_mailer(gc)
    if not mailer:
        print("  [notify] no local mail program found (SLURM MailProg / mail) "
              "— skipping email.")
        return False

    try:
        if Path(mailer).name == "sendmail":
            # sendmail reads the full message (incl. headers) from stdin.
            payload = f"To: {to_addr}\nSubject: {subject}\n\n{body}\n"
            subprocess.run([mailer, "-t"], input=payload, text=True,
                           timeout=30, check=True)
        else:
            # mail / mailx convention: `mail -s SUBJECT RECIPIENT`, body on stdin.
            subprocess.run([mailer, "-s", subject, to_addr], input=body, text=True,
                           timeout=30, check=True)
        print(f"  [notify] emailed {to_addr} via {mailer}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  [notify] email failed: {e}")
        return False
