# actions/send_email.py

import json
import smtplib
import sys
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG_PATH = _get_base_dir() / "config" / "api_keys.json"

SETUP_HELP = (
    "Email is not configured yet. Open config/api_keys.json and fill in "
    "email_app_password with a Gmail App Password "
    "(create one at myaccount.google.com/apppasswords)."
)


def _speak_and_log(message: str, player=None):
    if player:
        try:
            player.write_log(f"JARVIS: {message}")
        except Exception:
            pass
    print(f"[SendEmail] {message}")


def _load_email_config() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        "sender":     str(data.get("email_sender", "")).strip(),
        "password":   str(data.get("email_app_password", "")).replace(" ", "").strip(),
        "default_to": str(data.get("email_default_to", "")).strip(),
        "smtp_host":  str(data.get("email_smtp_host", "smtp.gmail.com")).strip(),
        "smtp_port":  int(data.get("email_smtp_port", 465)),
    }


def send_email(parameters: dict, player=None):
    """
    Sends an email.

    parameters:
        subject (required) — subject line
        body    (required) — message text
        to      (optional) — recipient address; defaults to the owner's address
                             from config (email_default_to)
    """
    subject = str(parameters.get("subject", "")).strip()
    body    = str(parameters.get("body", "")).strip()
    to      = str(parameters.get("to", "")).strip()

    if not subject and not body:
        msg = "Sir, I need at least a subject or a message for the email."
        _speak_and_log(msg, player)
        return msg
    if not subject:
        subject = "Message from JARVIS"

    cfg = _load_email_config()
    if not to:
        to = cfg["default_to"]
    if not to:
        msg = "Sir, I don't know who to send the email to — no recipient configured."
        _speak_and_log(msg, player)
        return msg

    if not cfg["sender"] or not cfg["password"]:
        _speak_and_log(SETUP_HELP, player)
        return SETUP_HELP

    message = EmailMessage()
    message["From"]    = formataddr(("JARVIS", cfg["sender"]))
    message["To"]      = to
    message["Subject"] = subject
    message.set_content(body or subject)

    try:
        if cfg["smtp_port"] == 465:
            with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"], timeout=30) as smtp:
                smtp.login(cfg["sender"], cfg["password"])
                smtp.send_message(message)
        else:
            with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=30) as smtp:
                smtp.starttls()
                smtp.login(cfg["sender"], cfg["password"])
                smtp.send_message(message)
    except smtplib.SMTPAuthenticationError:
        msg = ("Sir, the email login was rejected. Check email_sender and "
               "email_app_password in config/api_keys.json — the password must be "
               "a Gmail App Password, not your normal password.")
        _speak_and_log(msg, player)
        return msg
    except Exception as e:
        msg = f"Sir, I couldn't send the email: {e}"
        _speak_and_log(msg, player)
        return msg

    msg = f"Email sent to {to}: {subject}"
    _speak_and_log(msg, player)
    return msg
