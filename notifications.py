import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone

log = logging.getLogger(__name__)

SMTP_FROM = os.getenv("NOTIFY_EMAIL_FROM")
SMTP_TO = os.getenv("NOTIFY_EMAIL_TO")
SMTP_HOST = os.getenv("NOTIFY_SMTP_HOST")
SMTP_PORT = int(os.getenv("NOTIFY_SMTP_PORT", "587"))
SMTP_PASSWORD = os.getenv("NOTIFY_SMTP_PASSWORD")


def _config_available() -> bool:
    if not SMTP_FROM or not SMTP_TO or not SMTP_HOST or not SMTP_PASSWORD:
        log.warning("Email notification skipped: missing SMTP configuration.")
        return False
    return True


def send_price_alert(product_name: str, event_type: str, details: dict) -> bool:
    if not _config_available():
        return False

    subject = f"PriceSync Alert: {event_type} — {product_name}"
    timestamp = datetime.now(timezone.utc).isoformat()

    plain_lines = [
        f"Event: {event_type}",
        f"Product: {product_name}",
        "",
        "Details:",
    ]
    html_lines = [
        f"<h2>PriceSync Alert: {event_type}</h2>",
        f"<p><strong>Product:</strong> {product_name}</p>",
        "<h3>Details</h3>",
        "<ul>",
    ]

    for key, value in details.items():
        plain_lines.append(f"{key}: {value}")
        html_lines.append(f"<li><strong>{key}:</strong> {value}</li>")

    plain_lines.extend(["", f"Timestamp: {timestamp}"])
    html_lines.append("</ul>")
    html_lines.append(f"<p>Timestamp: {timestamp}</p>")

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = SMTP_FROM
    message["To"] = SMTP_TO
    message.attach(MIMEText("\n".join(plain_lines), "plain"))
    message.attach(MIMEText("".join(html_lines), "html"))

    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=3) as smtp:
                smtp.login(SMTP_FROM, SMTP_PASSWORD)
                smtp.sendmail(SMTP_FROM, [SMTP_TO], message.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=3) as smtp:
                smtp.starttls()
                smtp.login(SMTP_FROM, SMTP_PASSWORD)
                smtp.sendmail(SMTP_FROM, [SMTP_TO], message.as_string())
        log.info("Price alert email sent for %s", product_name)
        return True
    except Exception as exc:
        log.warning("Failed to send price alert email: %s", exc)
        return False
