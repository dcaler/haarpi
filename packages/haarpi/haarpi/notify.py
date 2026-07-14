"""Optional email notifications via the local mail program.

WHO REPORTS WHAT. There are two notifiers on this pipeline and they must not say the same
thing twice. The author was getting every finished task reported to him twice, seconds apart,
from two different addresses:

  TRUNDLR reports that a TASK finished. It PATCHes the task to done/failed and mails the
    exit code, the duration and the log tail. It is the ALARM, and it is the only thing that
    can report a crash — a tool that dies never reaches its own last line.

  HAARPI reports what it DECIDED. A gate passed and released X; the annotations classified to
    tier T and queued chain C. That is information trundlr cannot have, and it is the one
    mail worth reading.

  A TOOL DOES NOT REPORT ITS OWN COMPLETION. "raconteur one-pager done" and "rabbitHole
    gather complete" carried nothing trundlr had not just said, and arrived seconds behind
    it. They are gone. (They also fired on every manual run, so debugging the pipeline mailed
    the author each time.)

If you are adding a send_email() to a tool verb, the question to answer first is: what does
this say that "DONE: <task title>" does not?

Piggybacks on whatever mailer the server already uses for SLURM job mail (SLURM's
`MailProg`), so the tools need no SMTP credentials of their own. Resolution order for the
mail command:

  1. mail_prog        (the tool's config / env override)
  2. SLURM's MailProg, read from `scontrol show config`
  3. the first of `mail`, `mailx`, `sendmail` found on PATH

If no recipient or no mailer is found, send_email() is a no-op and never
raises — notifications must not break the pipeline.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


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


def resolve_mailer(mail_prog: str = "") -> str:
    if mail_prog:
        return mail_prog
    slurm = _slurm_mailprog()
    if slurm:
        return slurm
    for cand in ("mail", "mailx", "sendmail"):
        found = shutil.which(cand)
        if found:
            return found
    return ""


def send_email(subject: str, body: str, *, to: str, mail_prog: str = "") -> bool:
    if not to:
        return False

    mailer = resolve_mailer(mail_prog)
    if not mailer:
        print("  [notify] no local mail program found (SLURM MailProg / mail) "
              "— skipping email.")
        return False

    try:
        if Path(mailer).name == "sendmail":
            # sendmail reads the full message (incl. headers) from stdin.
            payload = f"To: {to}\nSubject: {subject}\n\n{body}\n"
            subprocess.run([mailer, "-t"], input=payload, text=True,
                           timeout=30, check=True)
        else:
            # mail / mailx convention: `mail -s SUBJECT RECIPIENT`, body on stdin.
            subprocess.run([mailer, "-s", subject, to], input=body, text=True,
                           timeout=30, check=True)
        print(f"  [notify] emailed {to} via {mailer}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  [notify] email failed: {e}")
        return False
