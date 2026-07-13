"""Notification sender for job-star check-ins.

Sends check-in notifications via the local SMTP email gateway (localhost:2525),
which fans out to Gmail (email delivery) and Google Chat. The notification
includes a link to a web page where the user can discuss the check-in with
an LLM and submit their response.

The email gateway is the vikunja-mail-gateway on port 2525 (mail.craigdfrench.com).
It accepts SMTP with AUTH LOGIN/PLAIN and delivers via Gmail XOAUTH2 + Chat fan-out.
"""

from __future__ import annotations

import os
import smtplib
import logging
from email.message import EmailMessage
from email.utils import formataddr
from typing import Optional

from .checkin import CheckIn, CheckInType, get_check_in
from .db import get_goal, audit

log = logging.getLogger("job-star.notify")

# Email gateway (local SMTP relay)
SMTP_HOST = os.environ.get("JOB_STAR_SMTP_HOST", "127.0.0.1")
SMTP_PORT = int(os.environ.get("JOB_STAR_SMTP_PORT", "2525"))
SMTP_USER = os.environ.get("JOB_STAR_SMTP_USER", "")
SMTP_PASS = os.environ.get("JOB_STAR_SMTP_PASS", "")

# Where the web page lives (tailnet URL)
WEB_BASE = os.environ.get("JOB_STAR_WEB_BASE", "http://job-star.craigdfrench.com")

# Who receives the notifications
NOTIFY_TO = os.environ.get("JOB_STAR_NOTIFY_TO", "craig@thefrenches.ca")
NOTIFY_FROM = os.environ.get("JOB_STAR_NOTIFY_FROM", "job-star@thefrenches.ca")

TYPE_ICONS = {
    CheckInType.PROGRESS: "📊",
    CheckInType.CLARIFICATION: "❓",
    CheckInType.MILESTONE: "🏁",
    CheckInType.COMPLETION: "✅",
}

TYPE_SUBJECTS = {
    CheckInType.PROGRESS: "Progress update",
    CheckInType.CLARIFICATION: "Needs your input",
    CheckInType.MILESTONE: "Milestone reached",
    CheckInType.COMPLETION: "Ready for review",
}


async def send_check_in_notification(check_in_id: str) -> bool:
    """Send a check-in notification via email + chat (through the email gateway).

    Returns True on success, False on failure.
    """
    check_in = await get_check_in(check_in_id)
    if not check_in:
        log.error(f"Check-in not found: {check_in_id}")
        return False

    goal = await get_goal(check_in.goal_id)
    goal_title = goal.title if goal else check_in.goal_id[:8]

    icon = TYPE_ICONS.get(check_in.type, "📋")
    subject_prefix = TYPE_SUBJECTS.get(check_in.type, "Check-in")

    # Build the web link
    web_link = f"{WEB_BASE}/checkin/{check_in.id}"

    # Build the email body
    body_lines = [
        f"{icon} Job-Star {subject_prefix}: {goal_title}",
        "",
        f"Check-in ID: {check_in.id[:8]}",
        f"Type: {check_in.type.value}",
        "",
    ]

    if check_in.progress_summary:
        body_lines.append("PROGRESS SUMMARY:")
        body_lines.append(check_in.progress_summary)
        body_lines.append("")

    if check_in.results:
        body_lines.append("RESULTS:")
        body_lines.append(check_in.results[:500])
        body_lines.append("")

    if check_in.next_steps:
        body_lines.append("NEXT STEPS:")
        body_lines.append(check_in.next_steps)
        body_lines.append("")

    if check_in.questions:
        body_lines.append("QUESTIONS:")
        for i, q in enumerate(check_in.questions, 1):
            body_lines.append(f"  {i}. {q.question}")
            if q.options:
                for j, opt in enumerate(q.options, 1):
                    body_lines.append(f"     {j}) {opt}")
        body_lines.append("")

    body_lines.append("─" * 50)
    body_lines.append("")
    body_lines.append("Discuss and respond in your browser:")
    body_lines.append(f"  {web_link}")
    body_lines.append("")

    body = "\n".join(body_lines)

    # Build the email
    msg = EmailMessage()
    msg["From"] = formataddr(("Job-Star", NOTIFY_FROM))
    msg["To"] = NOTIFY_TO
    msg["Subject"] = f"{icon} {subject_prefix}: {goal_title}"
    msg.set_content(body)

    # Send via local SMTP gateway
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            if SMTP_USER and SMTP_PASS:
                server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)

        await audit("checkin_notified", {
            "check_in_id": check_in_id,
            "goal_id": check_in.goal_id,
            "type": check_in.type.value,
            "to": NOTIFY_TO,
        }, check_in.goal_id)

        log.info(f"Sent notification for check-in {check_in_id[:8]} to {NOTIFY_TO}")
        return True

    except Exception as e:
        log.error(f"Failed to send notification: {e}")
        await audit("checkin_notify_failed", {
            "check_in_id": check_in_id,
            "error": str(e)[:200],
        }, check_in.goal_id)
        return False