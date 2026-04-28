import json
import os
import re
import sys
from base64 import urlsafe_b64decode
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


for _env_key, _env_value in load_dotenv(Path(__file__).with_name(".env")).items():
    os.environ.setdefault(_env_key, _env_value)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")
BOT_TOKEN = os.getenv("BUBBLES_BOT_TOKEN", "")
ALLOWED_USER_ID_TEXT = os.getenv("BUBBLES_ALLOWED_USER_ID", "").strip()
ALLOWED_USER_ID = int(ALLOWED_USER_ID_TEXT) if ALLOWED_USER_ID_TEXT.isdigit() else None
STATE_PATH = Path(os.getenv("BUBBLES_STATE_PATH", "assistant_state.json"))

GMAIL_ACCOUNTS = [item.strip() for item in os.getenv("GMAIL_ACCOUNTS", "").split(",") if item.strip()]
OUTLOOK_ACCOUNTS = [item.strip() for item in os.getenv("OUTLOOK_ACCOUNTS", "").split(",") if item.strip()]
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
GOOGLE_CREDENTIALS_PATH = Path(os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json"))
GOOGLE_TOKEN_PATH = Path(os.getenv("GOOGLE_TOKEN_PATH", "token.json"))
GOOGLE_CALENDAR_TIMEZONE = os.getenv("GOOGLE_CALENDAR_TIMEZONE", "America/Toronto")
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_FETCH_LIMIT = max(1, min(int(os.getenv("GMAIL_FETCH_LIMIT", "10")), 25))
GMAIL_QUERY = os.getenv("GMAIL_QUERY", "newer_than:2d")
GMAIL_UNREAD_QUERY = os.getenv("GMAIL_UNREAD_QUERY", f"{GMAIL_QUERY} is:unread")
GMAIL_FULL_UNREAD_QUERY = "is:unread"
try:
    LOCAL_TZ = ZoneInfo(GOOGLE_CALENDAR_TIMEZONE)
except ZoneInfoNotFoundError:
    LOCAL_TZ = timezone.utc

MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}
WEEKDAYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}


@dataclass
class EmailItem:
    provider: str
    account: str
    message_id: str
    thread_id: str
    sender: str
    subject: str
    received_at: str
    snippet: str
    raw_body: str = ""
    label_ids: tuple[str, ...] = ()
    unread: bool = False


@dataclass
class EventDraft:
    title: str
    start_iso: str
    end_iso: str
    location: str = ""
    description: str = ""
    source_provider: str = ""
    source_account: str = ""
    source_message_id: str = ""
    calendar_event_id: str = ""


@dataclass
class AccountError:
    provider: str
    account: str
    message: str


class LocalState:
    def __init__(self, path: Path):
        self.path = path
        self.data = {
            "processed_messages": {},
            "created_events": {},
            "created_docs": {},
        }
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True), encoding="utf-8")

    def seen_message(self, provider: str, account: str, message_id: str) -> bool:
        return message_id in self.data.get("processed_messages", {}).get(f"{provider}:{account}", {})

    def mark_message(self, provider: str, account: str, message_id: str, payload: dict[str, Any]) -> None:
        key = f"{provider}:{account}"
        self.data.setdefault("processed_messages", {}).setdefault(key, {})[message_id] = payload
        self.save()

    def record_event(self, key: str, payload: dict[str, Any]) -> None:
        self.data.setdefault("created_events", {})[key] = payload
        self.save()

    def record_doc(self, key: str, payload: dict[str, Any]) -> None:
        self.data.setdefault("created_docs", {})[key] = payload
        self.save()


state = LocalState(STATE_PATH)


def is_authorized(update: Update) -> bool:
    user = update.effective_user
    return bool(user and ALLOWED_USER_ID and user.id == ALLOWED_USER_ID)


def ask_ollama(prompt: str) -> str:
    try:
        response = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=120,
        )
        response.raise_for_status()
        return response.json().get("response", "").strip() or "No response from Ollama."
    except Exception as e:
        return f"Ollama error: {e}"


def parse_iso(dt_text: str) -> Optional[datetime]:
    dt_text = dt_text.strip()
    try:
        if dt_text.endswith("Z"):
            return datetime.fromisoformat(dt_text.replace("Z", "+00:00"))
        return datetime.fromisoformat(dt_text)
    except Exception:
        return None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def humanize_email(item: EmailItem) -> str:
    prompt = (
        "You are a concise personal assistant. Rewrite this email as a short human summary. "
        "Focus on the important action, date, time, and sender. "
        "Do not invent details.\n\n"
        f"From: {item.sender}\n"
        f"Subject: {item.subject}\n"
        f"Received: {item.received_at}\n"
        f"Snippet: {item.snippet}\n"
    )
    return ask_ollama(prompt)


def summarize_digest(items: list[EmailItem]) -> str:
    if not items:
        return "No recent mail to summarize."
    prompt = (
        "You are a concise executive assistant. Summarize these emails into a short digest. "
        "Group by account. For each account, call out urgent items, deadlines, requests, and scheduling details. "
        "Use plain text with short bullets. Do not invent details.\n\n"
    )
    for item in items[:20]:
        prompt += (
            f"Account: {item.account}\n"
            f"From: {item.sender}\n"
            f"Subject: {item.subject}\n"
            f"Received: {item.received_at}\n"
            f"Snippet: {item.snippet}\n\n"
        )
    return ask_ollama(prompt)


def ask_ollama_json(prompt: str) -> dict[str, Any]:
    raw = ask_ollama(prompt)
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end < start:
            return {}
        return json.loads(raw[start : end + 1])
    except Exception:
        return {}


def extract_event_from_text(item: EmailItem) -> Optional[EventDraft]:
    text = f"{item.subject}\n{item.snippet}\n{item.raw_body}"

    date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    time_match = re.search(r"\b(\d{1,2}:\d{2}\s?(?:AM|PM|am|pm)?)\b", text)
    title = item.subject.strip() or "Email event"

    if not date_match:
        return None

    date_part = date_match.group(1)
    time_part = time_match.group(1) if time_match else "09:00 AM"

    try:
        start = datetime.fromisoformat(f"{date_part}T{normalize_time(time_part)}")
    except Exception:
        return None

    end = start + timedelta(hours=1)
    location_match = re.search(r"\b(?:at|location:)\s*([A-Za-z0-9 ,.-]{3,80})", text, re.IGNORECASE)
    location = location_match.group(1).strip() if location_match else ""
    description = humanize_email(item)

    return EventDraft(
        title=title[:120],
        start_iso=start.isoformat(),
        end_iso=end.isoformat(),
        location=location[:200],
        description=description[:2000],
        source_provider=item.provider,
        source_account=item.account,
        source_message_id=item.message_id,
    )


def normalize_time(text: str) -> str:
    value = text.strip().upper().replace(" ", "")
    if value.endswith("AM") or value.endswith("PM"):
        meridian = value[-2:]
        digits = value[:-2]
        if ":" not in digits:
            digits = f"{digits}:00"
        hour, minute = digits.split(":", 1)
        hour_i = int(hour)
        if meridian == "PM" and hour_i != 12:
            hour_i += 12
        if meridian == "AM" and hour_i == 12:
            hour_i = 0
        return f"{hour_i:02d}:{int(minute):02d}:00"
    if ":" in value:
        hour, minute = value.split(":", 1)
        return f"{int(hour):02d}:{int(minute):02d}:00"
    return f"{int(value):02d}:00:00"


def gmail_headers(message: dict[str, Any]) -> dict[str, str]:
    headers = {}
    for header in message.get("payload", {}).get("headers", []):
        headers[header.get("name", "").lower()] = header.get("value", "")
    return headers


def decode_gmail_body(data: str) -> str:
    if not data:
        return ""
    padding = "=" * (-len(data) % 4)
    try:
        return urlsafe_b64decode(data + padding).decode("utf-8", errors="replace")
    except Exception:
        return ""


def extract_gmail_text(payload: dict[str, Any]) -> str:
    mime_type = payload.get("mimeType", "")
    body = decode_gmail_body((payload.get("body", {}) or {}).get("data", ""))
    if mime_type == "text/plain" and body.strip():
        return body

    parts = payload.get("parts", []) or []
    plain_chunks: list[str] = []
    html_chunks: list[str] = []
    for part in parts:
        nested = extract_gmail_text(part)
        if not nested.strip():
            continue
        if part.get("mimeType") == "text/plain":
            plain_chunks.append(nested)
        else:
            html_chunks.append(nested)

    combined = "\n".join(plain_chunks or html_chunks)
    if combined.strip():
        return combined

    return body


def env_first(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def token_data(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def token_scopes(path: Path) -> set[str]:
    scopes = token_data(path).get("scopes", [])
    if isinstance(scopes, str):
        return set(scopes.split())
    if isinstance(scopes, list):
        return {str(scope) for scope in scopes}
    return set()


def token_has_gmail_access(path: Path) -> bool:
    scopes = token_scopes(path)
    return GMAIL_MODIFY_SCOPE in scopes or GMAIL_READONLY_SCOPE in scopes


def token_account(path: Path) -> str:
    value = token_data(path).get("account", "")
    return str(value).strip()


def gmail_account_name() -> str:
    return os.getenv("GMAIL_ACCOUNT", "").strip() or token_account(GOOGLE_TOKEN_PATH) or "primary"


def gmail_configuration_issue() -> str:
    if os.getenv("GMAIL_ACCESS_TOKEN", "").strip() or os.getenv("GMAIL_TOKEN", "").strip():
        return ""
    if not GOOGLE_TOKEN_PATH.exists():
        return f"{GOOGLE_TOKEN_PATH} is missing. Run python3 bubbles.py --google-auth and complete Google OAuth."
    if not token_has_gmail_access(GOOGLE_TOKEN_PATH):
        return (
            f"{GOOGLE_TOKEN_PATH} is present, but Gmail access scope is missing. "
            f"Delete {GOOGLE_TOKEN_PATH}, run python3 bubbles.py --google-auth, then complete Google OAuth again."
        )
    return ""


def gmail_access_token() -> tuple[str, str]:
    direct_token = os.getenv("GMAIL_ACCESS_TOKEN", "").strip() or os.getenv("GMAIL_TOKEN", "").strip()
    if direct_token:
        return direct_token, ""

    issue = gmail_configuration_issue()
    if issue:
        return "", issue

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        return "", "Google auth libraries are not installed. Run: pip install -r requirements.txt"

    scopes = token_scopes(GOOGLE_TOKEN_PATH)
    scope = GMAIL_MODIFY_SCOPE if GMAIL_MODIFY_SCOPE in scopes else GMAIL_READONLY_SCOPE
    try:
        creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_PATH), [scope])
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        if creds and creds.valid and creds.token:
            return creds.token, ""
    except Exception as exc:
        return "", f"Gmail token could not be loaded: {exc.__class__.__name__}"
    return "", "Gmail token is present, but no valid access token was available."


def get_google_access_token(account: str, provider: str) -> str:
    account_key = slugify(account)
    provider_key = provider.upper()
    direct_token = env_first(
        f"{provider_key}_TOKEN_{account_key}",
        f"{provider_key}_ACCESS_TOKEN_{account_key}",
    )
    if direct_token:
        return direct_token

    refresh_token = env_first(
        f"{provider_key}_REFRESH_TOKEN_{account_key}",
        f"{provider_key}_TOKEN_REFRESH_{account_key}",
        f"{provider_key}_REFRESH_TOKEN",
    )
    client_id = env_first(
        f"{provider_key}_CLIENT_ID_{account_key}",
        f"{provider_key}_CLIENT_ID",
        "GOOGLE_CLIENT_ID",
    )
    client_secret = env_first(
        f"{provider_key}_CLIENT_SECRET_{account_key}",
        f"{provider_key}_CLIENT_SECRET",
        "GOOGLE_CLIENT_SECRET",
    )
    if not (refresh_token and client_id and client_secret):
        return ""

    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("access_token", "")


def fetch_gmail_messages(account: str, query: str | None = None, token: str | None = None) -> tuple[list[EmailItem], Optional[str]]:
    try:
        token = token or get_google_access_token(account, "gmail")
    except Exception as exc:
        return [], f"Gmail auth failed for {account}: {exc}"
    if not token:
        return [], (
            f"Gmail auth is not configured for {account}. Set either "
            f"GMAIL_TOKEN_{slugify(account)} or GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET plus "
            f"GMAIL_REFRESH_TOKEN_{slugify(account)}, or authorize {GOOGLE_TOKEN_PATH} with Gmail access."
        )
    try:
        resp = requests.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            headers={"Authorization": f"Bearer {token}"},
            params={"maxResults": GMAIL_FETCH_LIMIT, "q": query or GMAIL_QUERY},
            timeout=60,
        )
        resp.raise_for_status()
        messages = resp.json().get("messages", [])
        items: list[EmailItem] = []
        for msg in messages:
            detail = requests.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg['id']}",
                headers={"Authorization": f"Bearer {token}"},
                params={"format": "full"},
                timeout=60,
            )
            detail.raise_for_status()
            data = detail.json()
            headers = gmail_headers(data)
            payload = data.get("payload", {}) or {}
            raw_body = extract_gmail_text(payload).strip() or data.get("snippet", "")
            label_ids = tuple(str(label) for label in data.get("labelIds", []) or [])
            items.append(
                EmailItem(
                    provider="gmail",
                    account=account,
                    message_id=data["id"],
                    thread_id=data.get("threadId", data["id"]),
                    sender=headers.get("from", ""),
                    subject=headers.get("subject", ""),
                    received_at=headers.get("date", ""),
                    snippet=data.get("snippet", ""),
                    raw_body=raw_body[:5000],
                    label_ids=label_ids,
                    unread="UNREAD" in label_ids,
                )
            )
        return items, None
    except Exception as exc:
        return [], f"Gmail fetch failed for {account}: {exc}"


def fetch_outlook_messages(account: str) -> list[EmailItem]:
    token = os.getenv(f"OUTLOOK_TOKEN_{slugify(account)}", "")
    if not token:
        return []
    resp = requests.get(
        "https://graph.microsoft.com/v1.0/me/messages",
        headers={"Authorization": f"Bearer {token}"},
        params={"$top": 10, "$orderby": "receivedDateTime desc"},
        timeout=60,
    )
    resp.raise_for_status()
    items: list[EmailItem] = []
    for msg in resp.json().get("value", []):
        items.append(
            EmailItem(
                provider="outlook",
                account=account,
                message_id=msg["id"],
                thread_id=msg.get("conversationId", msg["id"]),
                sender=(msg.get("from", {}) or {}).get("emailAddress", {}).get("address", ""),
                subject=msg.get("subject", ""),
                received_at=msg.get("receivedDateTime", ""),
                snippet=msg.get("bodyPreview", ""),
                raw_body=msg.get("body", {}).get("content", ""),
            )
        )
    return items


def load_recent_mail() -> tuple[list[EmailItem], list[AccountError]]:
    items: list[EmailItem] = []
    errors: list[AccountError] = []

    for account in GMAIL_ACCOUNTS:
        messages, error = fetch_gmail_messages(account)
        items.extend(messages)
        if error:
            errors.append(AccountError(provider="gmail", account=account, message=error))

    for account in OUTLOOK_ACCOUNTS:
        try:
            items.extend(fetch_outlook_messages(account))
        except Exception as exc:
            errors.append(AccountError(provider="outlook", account=account, message=f"Outlook fetch failed for {account}: {exc}"))

    return items, errors


def slugify(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").upper()


def compact_text(value: str | None, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def email_text(item: EmailItem) -> str:
    return f"{item.sender} {item.subject} {item.snippet} {item.raw_body}".lower()


def visible_email_text(item: EmailItem) -> str:
    return f"{item.subject}\n{item.snippet}\n{item.raw_body}"


def sender_display(sender: str) -> str:
    sender = sender.strip()
    match = re.search(r"([^<]+)<", sender)
    if match:
        return compact_text(match.group(1).strip().strip('"'), 80)
    if "@" in sender:
        domain = sender.split("@", 1)[1].split(">", 1)[0]
        return compact_text(domain.split(".", 1)[0].replace("-", " ").title(), 80)
    return compact_text(sender, 80) or "Unknown"


def next_month_day(month: int, day: int) -> str:
    today = datetime.now(LOCAL_TZ).date()
    year = today.year
    candidate = datetime(year, month, day).date()
    if candidate < today:
        candidate = datetime(year + 1, month, day).date()
    return candidate.isoformat()


def next_weekday(weekday: int) -> str:
    today = datetime.now(LOCAL_TZ).date()
    days_ahead = (weekday - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return (today + timedelta(days=days_ahead)).isoformat()


def parse_human_date(value: str) -> str:
    text = value.strip().lower().replace(",", " ")
    text = re.sub(r"\s+", " ", text)
    today = datetime.now(LOCAL_TZ).date()

    if re.search(r"\btoday\b", text):
        return today.isoformat()
    if re.search(r"\btomorrow\b", text):
        return (today + timedelta(days=1)).isoformat()

    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if iso_match:
        return iso_match.group(1)

    for day_month in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]+)\b", text):
        if day_month.group(2) in MONTHS:
            return next_month_day(MONTHS[day_month.group(2)], int(day_month.group(1)))

    for month_day in re.finditer(r"\b([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\b", text):
        if month_day.group(1) in MONTHS:
            return next_month_day(MONTHS[month_day.group(1)], int(month_day.group(2)))

    for weekday_match in re.finditer(r"\b(?:next\s+)?([a-z]+)\b", text):
        if weekday_match.group(1) in WEEKDAYS:
            return next_weekday(WEEKDAYS[weekday_match.group(1)])

    return ""


def parse_human_time(value: str) -> str:
    text = value.strip().lower()
    text = text.replace("a.m.", "am").replace("a.m", "am")
    text = text.replace("p.m.", "pm").replace("p.m", "pm")
    text = re.sub(r"\ba\s*\.?\s*m\.?\b", "am", text)
    text = re.sub(r"\bp\s*\.?\s*m\.?\b", "pm", text)

    am_pm = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text)
    if am_pm:
        hour = int(am_pm.group(1))
        minute = int(am_pm.group(2) or "0")
        marker = am_pm.group(3)
        if hour == 12:
            hour = 0
        if marker == "pm":
            hour += 12
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"

    named_period = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s+in\s+the\s+(evening|afternoon|morning)\b", text)
    if named_period:
        hour = int(named_period.group(1))
        minute = int(named_period.group(2) or "0")
        period = named_period.group(3)
        if period in {"evening", "afternoon"} and hour < 12:
            hour += 12
        if period == "morning" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"

    twenty_four = re.search(r"\b(?:at\s+)?([01]?\d|2[0-3]):([0-5]\d)\b", text)
    if twenty_four:
        return f"{int(twenty_four.group(1)):02d}:{int(twenty_four.group(2)):02d}"

    return ""


def looks_like_time(value: str) -> bool:
    value = value.strip().lower()
    return bool(re.fullmatch(r"\d{1,2}(:\d{2})?\s*(am|pm)?", value) or re.fullmatch(r"\d{1,2}\s*(am|pm)", value))


def extract_meeting_link(text: str) -> str:
    match = re.search(r"https?://\S*(?:zoom|meet\.google|teams|webex|calendar)\S*", text, flags=re.IGNORECASE)
    if not match:
        match = re.search(r"https?://\S+", text, flags=re.IGNORECASE)
    return match.group(0).rstrip(").,") if match else ""


def extract_email_location(text: str) -> str:
    link = extract_meeting_link(text)
    if link:
        return link
    match = re.search(r"\b(?:location|where|at):?\s+([^\n.]{3,120})", text, flags=re.IGNORECASE)
    if match:
        location = match.group(1).strip()
        return "" if looks_like_time(location) else compact_text(location, 200)
    return ""


def detect_appointment_fields(item: EmailItem) -> dict[str, str]:
    text = visible_email_text(item)
    lowered = text.lower()
    appointment_words = (
        "appointment",
        "meeting",
        "meet",
        "check-in",
        "check in",
        "catch up",
        "schedule",
        "scheduled",
        "invite",
        "calendar",
        "interview",
        "webinar",
    )
    has_appointment_word = any(word in lowered for word in appointment_words)
    date_value = parse_human_date(text)
    time_value = parse_human_time(text)
    link = extract_meeting_link(text)
    if not has_appointment_word or not (date_value or time_value or link):
        return {"date": "", "time": "", "location": "", "link": ""}
    return {
        "date": date_value,
        "time": time_value,
        "location": extract_email_location(text),
        "link": link,
    }


def email_is_promotional(item: EmailItem) -> bool:
    text = email_text(item)
    promo_words = ("sale", "deal", "discount", "off", "coupon", "offer", "promo", "save", "limited time", "expires")
    weak_words = ("unsubscribe", "newsletter", "digest", "sponsored")
    return any(word in text for word in promo_words) and not all(word in text for word in weak_words)


def promo_score(item: EmailItem) -> int:
    text = email_text(item)
    score = 0
    strong_terms = ("50% off", "60% off", "70% off", "free shipping", "expires today", "last chance", "limited time")
    useful_terms = ("discount", "coupon", "sale", "deal", "offer", "save")
    spam_terms = ("crypto", "winner", "guaranteed", "act now", "risk-free")
    score += sum(2 for term in strong_terms if term in text)
    score += sum(1 for term in useful_terms if term in text)
    score -= sum(2 for term in spam_terms if term in text)
    if re.search(r"\b\d{2,3}%\s+off\b", text):
        score += 2
    return score


def email_type(item: EmailItem, appointment: dict[str, str]) -> str:
    text = email_text(item)
    sender = item.sender.lower()
    if any(appointment.values()):
        return "appointment"
    if any(word in text for word in ("security", "password", "sign-in", "login", "verification", "2fa", "account alert")):
        return "security"
    if any(word in text for word in ("deadline", "due today", "due tomorrow", "due by", "expires", "final notice")):
        return "deadline"
    if any(word in text for word in ("invoice", "bill", "statement", "balance due", "past due", "amount due")):
        return "bill"
    if any(word in text for word in ("payment", "receipt", "paid", "transaction", "charge", "refund")):
        return "payment"
    if email_is_promotional(item):
        return "promo"
    if sender and not any(word in sender for word in ("noreply", "no-reply", "donotreply", "newsletter")):
        return "personal"
    return "review"


def email_is_urgent(item: EmailItem, item_type: str) -> bool:
    text = email_text(item)
    if item_type in {"appointment", "bill", "deadline", "security"}:
        return True
    return any(word in text for word in ("urgent", "asap", "action required", "due today", "due tomorrow", "past due"))


def email_priority(item: EmailItem, item_type: str) -> str:
    if email_is_urgent(item, item_type):
        return "high"
    if item_type in {"payment", "personal"}:
        return "medium"
    return "low"


def email_emoji(item_type: str) -> str:
    return {
        "appointment": "📅",
        "bill": "💸",
        "payment": "💳",
        "deadline": "🔥",
        "security": "🔐",
        "promo": "🎁",
        "personal": "👤",
        "review": "📩",
    }.get(item_type, "📩")


def email_why_it_matters(item: EmailItem, item_type: str) -> str:
    if item_type == "appointment":
        return "It may affect your calendar."
    if item_type == "bill":
        return "It appears to be a bill or amount due."
    if item_type == "payment":
        return "It appears payment-related."
    if item_type == "deadline":
        return "It may include a deadline or requested action."
    if item_type == "security":
        return "It may be account or security related."
    if item_type == "promo":
        return "It looks promotional."
    if item_type == "personal":
        return "It looks like a direct message from a person or organization."
    return "It may be worth a quick review."


def email_action(item: EmailItem, item_type: str) -> str:
    if item_type == "appointment":
        return "Add to calendar"
    if item_type == "bill":
        due_match = re.search(r"\b(?:by|before|due)\s+([A-Z][a-z]+day|today|tomorrow|[A-Z][a-z]+\s+\d{1,2})", visible_email_text(item), flags=re.IGNORECASE)
        return f"Review bill due {compact_text(due_match.group(1), 40)}" if due_match else "Review bill"
    if item_type == "payment":
        return "Review payment"
    if item_type == "deadline":
        return "Handle deadline"
    if item_type == "security":
        return "Review security alert"
    if item_type == "promo":
        return "Review offer" if promo_score(item) >= 3 else "Ignore"
    if item_type == "personal":
        return "Reply / review"
    return "Review / optional"


def email_highlight(item: EmailItem, item_type: str) -> str:
    text = re.sub(r"\s+", " ", (item.snippet or item.raw_body or "").strip())
    subject = item.subject.strip()
    sentence = re.split(r"(?<=[.!?])\s+", text or subject, maxsplit=1)[0]
    prefix = {
        "appointment": "Scheduling: ",
        "bill": "Bill: ",
        "payment": "Payment: ",
        "deadline": "Deadline: ",
        "security": "Security: ",
        "promo": "Promo: ",
    }.get(item_type, "")
    return compact_text(prefix + sentence, 220) or "No preview text available."


def digest_item_score(item: dict[str, Any]) -> int:
    base = {
        "appointment": 120,
        "bill": 105,
        "deadline": 100,
        "security": 95,
        "payment": 85,
        "personal": 70,
        "review": 45,
        "promo": 20,
    }.get(str(item.get("type", "review")), 45)
    if item.get("priority") == "high":
        base += 30
    elif item.get("priority") == "medium":
        base += 10
    return base


def build_digest_item(item: EmailItem) -> dict[str, Any]:
    appointment = detect_appointment_fields(item)
    item_type = email_type(item, appointment)
    priority = email_priority(item, item_type)
    digest_item = {
        "id": f"{item.provider}:{item.account}:{item.message_id}",
        "provider": item.provider,
        "account": item.account,
        "message_id": item.message_id,
        "gmail_message_id": item.message_id if item.provider == "gmail" else "",
        "thread_id": item.thread_id,
        "type": item_type,
        "category": item_type,
        "priority": priority,
        "emoji": email_emoji(item_type),
        "subject": compact_text(item.subject or "(no subject)", 160),
        "from": compact_text(item.sender or "Unknown", 160),
        "from_display": sender_display(item.sender),
        "received_at": item.received_at,
        "unread": item.unread,
        "highlight": email_highlight(item, item_type),
        "why_it_matters": email_why_it_matters(item, item_type),
        "action": email_action(item, item_type),
        "date": appointment.get("date", ""),
        "time": appointment.get("time", ""),
        "location": appointment.get("location", ""),
        "link": appointment.get("link", ""),
    }
    digest_item["_score"] = digest_item_score(digest_item)
    return digest_item


def load_mailman_messages(query: str) -> tuple[list[EmailItem], list[AccountError]]:
    items: list[EmailItem] = []
    errors: list[AccountError] = []

    if GMAIL_ACCOUNTS:
        for account in GMAIL_ACCOUNTS:
            messages, error = fetch_gmail_messages(account, query=query)
            items.extend(messages)
            if error:
                errors.append(AccountError(provider="gmail", account=account, message=error))
    else:
        token, error = gmail_access_token()
        account = gmail_account_name()
        if token:
            messages, fetch_error = fetch_gmail_messages(account, query=query, token=token)
            items.extend(messages)
            if fetch_error:
                errors.append(AccountError(provider="gmail", account=account, message=fetch_error))
        else:
            errors.append(AccountError(provider="gmail", account=account, message=f"Gmail is not configured: {error}"))

    return items, errors


def normalize_digest_mode(mode: str | None) -> str:
    return "full_unread" if mode == "full_unread" else "recent"


def digest_query_for_mode(mode: str, unread_only: bool = True) -> str:
    if mode == "full_unread":
        return GMAIL_FULL_UNREAD_QUERY
    return GMAIL_UNREAD_QUERY if unread_only else GMAIL_QUERY


def build_mailman_digest(
    unread_only: bool = True,
    query: str | None = None,
    limit: int | None = None,
    mode: str = "recent",
) -> dict[str, Any]:
    digest_mode = normalize_digest_mode(mode)
    scan_query = digest_query_for_mode(digest_mode, unread_only)
    messages, account_errors = load_mailman_messages(scan_query)
    digest_items = [build_digest_item(item) for item in messages]
    digest_items.sort(key=lambda entry: (-int(entry.get("_score", 0)), str(entry.get("received_at", "")), str(entry.get("id", ""))))
    for rank, entry in enumerate(digest_items, start=1):
        entry["rank"] = rank
        entry.pop("_score", None)
    if limit is not None:
        digest_items = digest_items[:limit]

    summary = {
        "total": len(digest_items),
        "urgent": sum(1 for item in digest_items if item.get("priority") == "high"),
        "appointments": sum(1 for item in digest_items if item.get("type") == "appointment"),
        "bills": sum(1 for item in digest_items if item.get("type") == "bill"),
        "payments": sum(1 for item in digest_items if item.get("type") == "payment"),
        "deadlines": sum(1 for item in digest_items if item.get("type") == "deadline"),
        "security": sum(1 for item in digest_items if item.get("type") == "security"),
        "promos": sum(1 for item in digest_items if item.get("type") == "promo"),
        "personal": sum(1 for item in digest_items if item.get("type") == "personal"),
        "review": sum(1 for item in digest_items if item.get("type") == "review"),
    }
    return {
        "agent": "mailman",
        "mode": digest_mode,
        "generated_at": now_utc().replace(microsecond=0).isoformat(),
        "query": scan_query,
        "read_only": True,
        "summary": summary,
        "items": digest_items,
        "errors": [asdict(error) for error in account_errors],
    }


def create_google_calendar_event(draft: EventDraft) -> str:
    token = os.getenv("GOOGLE_TOKEN", "")
    if not token:
        return ""
    payload = {
        "summary": draft.title,
        "location": draft.location,
        "description": draft.description,
        "start": {"dateTime": draft.start_iso},
        "end": {"dateTime": draft.end_iso},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 24 * 60},
                {"method": "popup", "minutes": 60},
            ],
        },
    }
    resp = requests.post(
        f"https://www.googleapis.com/calendar/v3/calendars/{GOOGLE_CALENDAR_ID}/events",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("id", "")


def create_google_doc(title: str, content: str) -> str:
    token = os.getenv("GOOGLE_TOKEN", "")
    if not token:
        return ""
    metadata: dict[str, Any] = {
        "name": title,
        "mimeType": "application/vnd.google-apps.document",
    }
    if GOOGLE_DRIVE_FOLDER_ID:
        metadata["parents"] = [GOOGLE_DRIVE_FOLDER_ID]
    doc_resp = requests.post(
        "https://www.googleapis.com/drive/v3/files",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params={"fields": "id"},
        json=metadata,
        timeout=60,
    )
    doc_resp.raise_for_status()
    doc_id = doc_resp.json().get("id", "")
    if doc_id and content:
        requests.post(
            f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "requests": [
                    {
                        "insertText": {
                            "location": {"index": 1},
                            "text": content,
                        }
                    }
                ]
            },
            timeout=60,
        ).raise_for_status()
    return doc_id


def sync_accounts(mark_seen: bool = True, create_events: bool = True) -> dict[str, Any]:
    created_events: list[dict[str, Any]] = []
    summaries: list[str] = []
    errors: list[str] = []
    messages, account_errors = load_recent_mail()

    for error in account_errors:
        errors.append(error.message)

    for item in messages:
        if mark_seen and state.seen_message(item.provider, item.account, item.message_id):
            continue
        if mark_seen:
            state.mark_message(
                item.provider,
                item.account,
                item.message_id,
                {"subject": item.subject, "received_at": item.received_at},
            )
        summary = humanize_email(item)
        summaries.append(f"[{item.provider}:{item.account}] {item.subject} - {summary}")
        if create_events:
            draft = extract_event_from_text(item)
            if draft:
                event_id = create_google_calendar_event(draft)
                draft.calendar_event_id = event_id
                key = f"{item.provider}:{item.account}:{item.message_id}"
                state.record_event(key, asdict(draft))
                created_events.append(asdict(draft))
                if os.getenv("CREATE_DRIVE_DRAFTS", "0") == "1":
                    doc_id = create_google_doc(f"Event notes - {draft.title}", draft.description)
                    if doc_id:
                        state.record_doc(key, {"doc_id": doc_id, "title": draft.title})

    return {"summaries": summaries, "events": created_events, "errors": errors}


def render_sync_result(result: dict[str, Any]) -> str:
    lines = []
    if result["errors"]:
        lines.append("Account issues:")
        lines.extend(f"- {message}" for message in result["errors"][:10])
        lines.append("")
    if result["summaries"]:
        lines.append("Recent mail summaries:")
        lines.extend(result["summaries"][:10])
    if result["events"]:
        lines.append("")
        lines.append("Created calendar events:")
        for event in result["events"][:10]:
            lines.append(f"- {event['title']} @ {event['start_iso']} -> {event.get('calendar_event_id', '')}")
    if not lines:
        lines.append("No new mail or events found.")
    return "\n".join(lines)[:4000]


def render_summary_result(items: list[EmailItem], errors: list[AccountError]) -> str:
    if not items and not errors:
        return "No new mail to summarize."
    lines = []
    if errors:
        lines.append("Account issues:")
        lines.extend(f"- {error.message}" for error in errors[:10])
        lines.append("")
    if items:
        lines.append(summarize_digest(items))
    return "\n".join(lines)[:4000]


def get_status_text() -> str:
    return (
        f"Configured Gmail accounts: {len(GMAIL_ACCOUNTS)}\n"
        f"Configured Outlook accounts: {len(OUTLOOK_ACCOUNTS)}\n"
        f"Gmail query: {GMAIL_QUERY}\n"
        f"Gmail fetch limit: {GMAIL_FETCH_LIMIT}\n"
        f"Google Calendar target: {GOOGLE_CALENDAR_ID}\n"
        f"Google Drive folder: {GOOGLE_DRIVE_FOLDER_ID or '(default)'}\n"
        f"State file: {STATE_PATH}"
    )


def decide_agent_action(user_text: str) -> dict[str, Any]:
    prompt = (
        "You are an intent router for a Telegram personal assistant.\n"
        "Choose exactly one action and return strict JSON only.\n"
        "Allowed actions: summary_mail, sync_mail, status, create_doc, chat.\n"
        "Use create_doc only if the user clearly wants a Google Doc created.\n"
        "For create_doc, also return title and content fields.\n"
        "If unsure, use chat.\n\n"
        "Return this shape:\n"
        '{"action":"chat","title":"","content":"","reason":""}\n\n'
        f"User message: {user_text}"
    )
    decision = ask_ollama_json(prompt)
    action = decision.get("action", "chat")
    if action not in {"summary_mail", "sync_mail", "status", "create_doc", "chat"}:
        decision["action"] = "chat"
    return decision


async def handle_agent_request(user_text: str) -> str:
    decision = decide_agent_action(user_text)
    action = decision.get("action", "chat")

    if action == "summary_mail":
        items, errors = load_recent_mail()
        return render_summary_result(items, errors)

    if action == "sync_mail":
        result = sync_accounts(mark_seen=True, create_events=True)
        return render_sync_result(result)

    if action == "status":
        return get_status_text()

    if action == "create_doc":
        title = str(decision.get("title", "")).strip()
        content = str(decision.get("content", "")).strip()
        if not title or not content:
            return "I need both a document title and content to create a Google Doc."
        doc_id = create_google_doc(title, content)
        if not doc_id:
            return "Google Drive document could not be created. Check Google OAuth token configuration."
        state.record_doc(title, {"doc_id": doc_id, "created_at": now_utc().isoformat()})
        return f"Created Google Drive document: {doc_id}"

    return ask_ollama(
        "You are a personal assistant. Respond briefly and helpfully.\n\n"
        f"User message: {user_text.strip()}"
    )[:4000]


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    await update.message.reply_text(
        "Bubbles assistant is online.\n\n"
        "Commands:\n"
        "/sync - read Gmail and Outlook/Live mail, create calendar events, and summarize changes\n"
        "/summary - read recent mail and return a digest without creating events or marking mail as processed\n"
        "/status - show configured account scope\n"
        "/doc <title> | <content> - create a Google Drive document\n"
        "\n"
        "Email is read-only. Calendar events are auto-created for detected occasions."
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    await update.message.reply_text(get_status_text())


async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    result = sync_accounts(mark_seen=True, create_events=True)
    await update.message.reply_text(render_sync_result(result))


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    items, errors = load_recent_mail()
    await update.message.reply_text(render_summary_result(items, errors))


async def doc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    if not update.message or not update.message.text:
        return
    raw = update.message.text[len("/doc "):].strip()
    if "|" not in raw:
        await update.message.reply_text("Usage: /doc <title> | <content>")
        return
    title, content = [part.strip() for part in raw.split("|", 1)]
    if not title or not content:
        await update.message.reply_text("Title and content are required.")
        return
    doc_id = create_google_doc(title, content)
    if not doc_id:
        await update.message.reply_text("Google Drive document could not be created. Check Google OAuth token configuration.")
        return
    state.record_doc(title, {"doc_id": doc_id, "created_at": now_utc().isoformat()})
    await update.message.reply_text(f"Created Google Drive document: {doc_id}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update) or not update.message or not update.message.text:
        return
    reply = await handle_agent_request(update.message.text.strip())
    await update.message.reply_text(reply[:4000])


def main():
    if "--test" in sys.argv:
        digest = build_mailman_digest(unread_only=True)
        print(json.dumps(digest, indent=2, ensure_ascii=False))
        return

    if not BOT_TOKEN:
        raise RuntimeError("BUBBLES_BOT_TOKEN is not set.")
    if ALLOWED_USER_ID is None:
        raise RuntimeError("BUBBLES_ALLOWED_USER_ID is not set.")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("sync", sync_command))
    app.add_handler(CommandHandler("summary", summary_command))
    app.add_handler(CommandHandler("doc", doc_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bubbles assistant is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
