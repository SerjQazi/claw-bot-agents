import json
import os
import re
import requests
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


load_dotenv(Path(__file__).with_name(".env"))

DEFAULT_OLLAMA_URL = "http://localhost:11434/api/chat"


def ollama_chat_url(value: str | None) -> str:
    url = (value or DEFAULT_OLLAMA_URL).strip() or DEFAULT_OLLAMA_URL
    if url.rstrip("/").endswith("/api/generate"):
        return url.rstrip("/")[: -len("/api/generate")] + "/api/chat"
    return url


OLLAMA_URL = ollama_chat_url(os.getenv("OLLAMA_URL"))
MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")

ALLOWED_USER_ID_TEXT = os.getenv("BUBBLES_ALLOWED_USER_ID", "").strip()
ALLOWED_USER_ID = int(ALLOWED_USER_ID_TEXT) if ALLOWED_USER_ID_TEXT.isdigit() else None
BOT_TOKEN = os.getenv("BUBBLES_BOT_TOKEN")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
GOOGLE_CREDENTIALS_PATH = Path(os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json"))
GOOGLE_TOKEN_PATH = Path(os.getenv("GOOGLE_TOKEN_PATH", "token.json"))
GOOGLE_CALENDAR_TIMEZONE = os.getenv("GOOGLE_CALENDAR_TIMEZONE", "America/Toronto")
MEMORY_PATH = Path(os.getenv("BUBBLES_MEMORY_PATH", "memory.json"))
BUBBLES_ENABLE_DEV_COMMANDS = os.getenv("BUBBLES_ENABLE_DEV_COMMANDS", "").strip().lower() in {"1", "true", "yes", "on"}
CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
REMINDER_MINUTES = [10, 30, 60, 24 * 60, 7 * 24 * 60]
PENDING_EVENTS: dict[int, dict] = {}
CHAT_MEMORY: dict[int, list[dict[str, str]]] = {}
SYSTEM_PROMPT = (
    "You are Bubbles, my personal Telegram assistant. Reply naturally and keep answers short. "
    "The bot has local tools for calendar, files, and system checks; never claim you lack access "
    "when the code can handle the task. For tool tasks, assume the code has already handled them."
)
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
NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "fourteen": 14,
}
SMALL_NUMBER_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
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
APPOINTMENT_TYPE_ALIASES = {
    "dentist": "dental",
    "dental": "dental",
    "teeth": "dental",
    "orthodontist": "dental",
    "hair": "hair",
    "haircut": "hair",
    "hairstylist": "hair",
    "barber": "hair",
    "doctor": "doctor",
    "physician": "doctor",
    "medical": "doctor",
    "gym": "gym",
    "workout": "gym",
    "school": "school",
    "class": "school",
}
NO_REMINDER_PHRASES = {
    "no",
    "nah",
    "nope",
    "none",
    "skip",
    "no reminder",
    "no reminders",
    "don't remind me",
    "dont remind me",
    "do not remind me",
    "that's okay",
    "thats okay",
    "that is okay",
    "no thanks",
    "no thank you",
    "not needed",
}
YES_REMINDER_PHRASES = {
    "yes",
    "y",
    "yeah",
    "yep",
    "sure",
    "please",
    "yes please",
    "remind me",
}
SKIP_DESCRIPTION_PHRASES = {
    "no",
    "none",
    "skip",
    "nope",
    "no description",
    "no notes",
    "nothing",
}


def is_authorized(update: Update) -> bool:
    user = update.effective_user
    return user is not None and ALLOWED_USER_ID is not None and user.id == ALLOWED_USER_ID


def remember_message(user_id: int, role: str, content: str) -> None:
    history = CHAT_MEMORY.setdefault(user_id, [])
    history.append({"role": role, "content": content})
    del history[:-10]


def load_memory() -> dict:
    if not MEMORY_PATH.exists():
        return {"appointment_defaults": {}}
    try:
        data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"appointment_defaults": {}}
    if not isinstance(data, dict):
        return {"appointment_defaults": {}}
    data.setdefault("appointment_defaults", {})
    return data


def save_memory(data: dict) -> None:
    data.setdefault("appointment_defaults", {})
    MEMORY_PATH.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def appointment_type_from_text(value: str) -> str | None:
    lowered = value.lower()
    for alias, appointment_type in APPOINTMENT_TYPE_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            return appointment_type
    return None


def clean_location_text(value: str) -> str:
    cleaned = value.strip(" .")
    cleaned = re.sub(
        r"^(at|location is|it's at|it is at|please|pls)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\s+(is my usual|as my|for my|for the|location)\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = cleaned.strip(" .")
    if normalize_reply(cleaned) == "virtual":
        return "Virtual"
    return cleaned


def normalize_reply(value: str) -> str:
    lowered = value.strip().lower()
    lowered = re.sub(r"[.!?]+$", "", lowered)
    return re.sub(r"\s+", " ", lowered)


def is_no_reminder_reply(value: str) -> bool:
    normalized = normalize_reply(value)
    if normalized in NO_REMINDER_PHRASES:
        return True
    if normalized.startswith(("nah", "no thanks", "no thank you")):
        return True
    return any(
        phrase in normalized
        for phrase in (
            "no reminder",
            "no reminders",
            "don't remind",
            "dont remind",
            "do not remind",
            "don't want to remind",
            "dont want to remind",
            "do not want to remind",
            "that's okay",
            "thats okay",
        )
    )


def is_yes_reminder_without_details(value: str) -> bool:
    return normalize_reply(value) in YES_REMINDER_PHRASES


def is_skip_location_reply(value: str) -> bool:
    return normalize_reply(value) in {"no", "none", "skip", "nope", "no location"}


def is_skip_description_reply(value: str) -> bool:
    return normalize_reply(value) in SKIP_DESCRIPTION_PHRASES


def extract_location_reply(value: str, appointment_type: str | None) -> tuple[str, bool, str | None]:
    lowered = normalize_reply(value)
    if is_skip_location_reply(value):
        return "", False, None

    if lowered in {"same as usual", "usual", "normal location", "use my usual location"}:
        location = usual_location_for_type(appointment_type)
        return location, False, None

    memory_fact = parse_memory_fact(value)
    if memory_fact:
        fact_type, location = memory_fact
        return location, True, fact_type

    at_match = re.search(r"\bat\s+(?P<location>.+)$", value.strip(), flags=re.IGNORECASE)
    if at_match:
        return clean_location_text(at_match.group("location")), False, None

    return clean_location_text(value), False, None


def appointment_defaults(appointment_type: str | None) -> dict:
    if not appointment_type:
        return {}
    defaults = load_memory().get("appointment_defaults", {}).get(appointment_type, {})
    return defaults if isinstance(defaults, dict) else {}


def usual_location_for_type(appointment_type: str | None) -> str:
    defaults = appointment_defaults(appointment_type)
    return str(defaults.get("usual_location") or defaults.get("location") or "").strip()


def update_appointment_default(appointment_type: str, key: str, value) -> None:
    if not appointment_type or value in ("", None):
        return
    memory = load_memory()
    defaults = memory.setdefault("appointment_defaults", {}).setdefault(appointment_type, {})
    defaults[key] = value
    if key == "location":
        defaults["usual_location"] = value
    save_memory(memory)


def parse_memory_fact(value: str) -> tuple[str, str] | None:
    text = value.strip()
    patterns = [
        r"(?P<location>.+?)\s+is\s+my\s+usual\s+(?P<kind>[a-z ]+?)\s+location\b",
        r"(?:please\s+)?add\s+(?P<location>.+?)\s+as\s+my\s+(?P<kind>[a-z ]+?)\b",
    ]
    match = None
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            break
    if not match:
        return None
    appointment_type = appointment_type_from_text(match.group("kind"))
    location = clean_location_text(match.group("location"))
    if not appointment_type or not location:
        return None
    return appointment_type, location


def build_ollama_messages(user_id: int, user_input: str) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in CHAT_MEMORY.get(user_id, []):
        role = item.get("role")
        if role not in {"user", "assistant"}:
            continue
        messages.append({"role": role, "content": item.get("content", "")})
    messages.append({"role": "user", "content": user_input})
    return messages


def ask_ollama(user_id: int, user_input: str) -> str:
    messages = build_ollama_messages(user_id, user_input)
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "messages": messages,
                "stream": False,
            },
            timeout=120
        )
        response.raise_for_status()
        data = response.json()
        message = data.get("message", {})
        return message.get("content", "").strip() or "No response from Ollama."
    except Exception as e:
        return f"❌ Ollama error: {e}"


def run_command(cmd: list[str]) -> str:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            check=True
        )
        return result.stdout.strip() or "✅ Done."
    except subprocess.CalledProcessError as e:
        return e.stdout.strip() or e.stderr.strip() or f"❌ Command failed: {e.returncode}"
    except Exception as e:
        return f"❌ Error: {e}"


def token_has_calendar_scopes(path: Path) -> bool:
    try:
        token_data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    granted_scopes = set(token_data.get("scopes", []))
    return all(scope in granted_scopes for scope in CALENDAR_SCOPES)


def get_calendar_service():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as e:
        raise RuntimeError(
            "Google Calendar libraries are not installed. Run: "
            "pip install -r requirements.txt"
        ) from e

    creds = None
    if GOOGLE_TOKEN_PATH.exists() and token_has_calendar_scopes(GOOGLE_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_PATH), CALENDAR_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            GOOGLE_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise RuntimeError(
                "Google Calendar is not authorized yet, or the existing token "
                "does not include event write access. Put your OAuth desktop "
                f"credentials at {GOOGLE_CREDENTIALS_PATH}, delete {GOOGLE_TOKEN_PATH}, and run: "
                "python3 bubbles.py --google-auth"
            )

    return build("calendar", "v3", credentials=creds)


def setup_google_calendar_auth() -> str:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as e:
        raise RuntimeError(
            "Google Calendar libraries are not installed. Run: "
            "pip install -r requirements.txt"
        ) from e

    if not GOOGLE_CREDENTIALS_PATH.exists():
        raise RuntimeError(
            f"Missing {GOOGLE_CREDENTIALS_PATH}. Download an OAuth desktop client "
            "JSON file from Google Cloud and save it there."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(GOOGLE_CREDENTIALS_PATH), CALENDAR_SCOPES)
    creds = flow.run_local_server(port=0)
    GOOGLE_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return f"Google Calendar authorized. Token saved to {GOOGLE_TOKEN_PATH}."


def list_calendar_events(days: int = 7, max_results: int = 10) -> list[dict]:
    service = get_calendar_service()
    now = datetime.now(timezone.utc)
    time_max = now + timedelta(days=max(1, min(days, 60)))
    result = (
        service.events()
        .list(
            calendarId=GOOGLE_CALENDAR_ID,
            timeMin=now.isoformat(),
            timeMax=time_max.isoformat(),
            maxResults=max(1, min(max_results, 25)),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return result.get("items", [])


def event_start_text(event: dict) -> str:
    start = event.get("start", {})
    return start.get("dateTime") or start.get("date") or "unknown time"


def parse_event_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo:
        return parsed.astimezone(LOCAL_TZ).replace(tzinfo=None)
    return parsed


def format_event_for_telegram(event: dict) -> str:
    title = event.get("summary", "(no title)")
    lines = [f"📌 {title}"]
    start = event.get("start", {})
    end = event.get("end", {})
    start_dt = parse_event_datetime(start.get("dateTime", ""))
    end_dt = parse_event_datetime(end.get("dateTime", ""))
    if start_dt:
        lines.append(f"🗓️ {start_dt.strftime('%A')}, {start_dt.strftime('%B')} {start_dt.day}")
        if end_dt:
            lines.append(f"⏰ {format_local_clock(start_dt.strftime('%H:%M'))} - {format_local_clock(end_dt.strftime('%H:%M'))}")
        else:
            lines.append(f"⏰ {format_local_clock(start_dt.strftime('%H:%M'))}")
    elif start.get("date"):
        try:
            date_only = datetime.fromisoformat(start["date"])
            lines.append(f"🗓️ {date_only.strftime('%A')}, {date_only.strftime('%B')} {date_only.day}")
        except ValueError:
            lines.append(f"🗓️ {start['date']}")
    if event.get("location"):
        lines.append(f"📍 {event['location']}")
    reminder_text = format_event_reminders(event)
    if reminder_text:
        lines.append(f"🔔 {reminder_text}")
    return "\n\n".join(lines)


def format_event_reminders(event: dict) -> str:
    reminders = event.get("reminders", {})
    if not isinstance(reminders, dict):
        return ""
    overrides = reminders.get("overrides")
    if not isinstance(overrides, list):
        return ""
    offsets = []
    for reminder in overrides:
        if not isinstance(reminder, dict):
            continue
        minutes = reminder.get("minutes")
        if isinstance(minutes, int):
            offsets.append(minutes)
    return format_reminder_offsets(offsets) if offsets else "No reminders"


def format_calendar_events(events: list[dict], empty_message: str = "No upcoming events found.") -> str:
    if not events:
        return empty_message

    return "\n\n".join(format_event_for_telegram(event) for event in events)


def parse_calendar_date(value: str) -> tuple[dict, dict]:
    raw = value.strip()
    normalized = raw.replace("Z", "+00:00")

    if len(raw) == 10:
        start_date = datetime.strptime(raw, "%Y-%m-%d").date()
        end_date = start_date + timedelta(days=1)
        return {"date": start_date.isoformat()}, {"date": end_date.isoformat()}

    for candidate in (normalized, normalized.replace(" ", "T", 1)):
        try:
            start = datetime.fromisoformat(candidate)
            break
        except ValueError:
            start = None
    if start is None:
        raise ValueError("Use YYYY-MM-DD, YYYY-MM-DD HH:MM, or YYYY-MM-DDTHH:MM.")

    end = start + timedelta(hours=1)
    start_value = {"dateTime": start.replace(tzinfo=None).isoformat(), "timeZone": GOOGLE_CALENDAR_TIMEZONE}
    end_value = {"dateTime": end.replace(tzinfo=None).isoformat(), "timeZone": GOOGLE_CALENDAR_TIMEZONE}
    return start_value, end_value


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


def title_case_event(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value.strip())
    cleaned = re.sub(r"^(an?|the)\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned[:1].upper() + cleaned[1:] if cleaned else cleaned


def parse_human_date(value: str) -> str | None:
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

    for weekday_match in re.finditer(r"\b(?:next\s+)?([a-z]+)\b", text):
        if weekday_match.group(1) in WEEKDAYS:
            return next_weekday(WEEKDAYS[weekday_match.group(1)])

    day_month = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]+)\b", text)
    if day_month and day_month.group(2) in MONTHS:
        return next_month_day(MONTHS[day_month.group(2)], int(day_month.group(1)))

    month_day = re.search(r"\b([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\b", text)
    if month_day and month_day.group(1) in MONTHS:
        return next_month_day(MONTHS[month_day.group(1)], int(month_day.group(2)))

    return None


def parse_human_time(value: str) -> str | None:
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

    evening = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s+in\s+the\s+(evening|afternoon|morning)\b", text)
    if evening:
        hour = int(evening.group(1))
        minute = int(evening.group(2) or "0")
        period = evening.group(3)
        if period in {"evening", "afternoon"} and hour < 12:
            hour += 12
        if period == "morning" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"

    twenty_four = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
    if twenty_four:
        return f"{int(twenty_four.group(1)):02d}:{int(twenty_four.group(2)):02d}"

    at_hour = re.search(r"\bat\s+(\d{1,2})\b", text)
    if at_hour:
        hour = int(at_hour.group(1))
        if 1 <= hour <= 23:
            return f"{hour:02d}:00"

    return None


def format_local_event_time(date_text: str, time_text: str) -> str:
    start = datetime.fromisoformat(f"{date_text}T{time_text}")
    month = start.strftime("%B")
    day = start.day
    hour = start.strftime("%I").lstrip("0")
    minute = start.strftime("%M")
    marker = start.strftime("%p")
    return f"{month} {day} at {hour}:{minute} {marker}"


def format_local_event_date(date_text: str) -> str:
    start = datetime.fromisoformat(date_text)
    return f"{start.strftime('%A')}, {start.strftime('%B')} {start.day}"


def format_local_clock(time_text: str) -> str:
    start = datetime.fromisoformat(f"2000-01-01T{time_text}")
    return f"{start.strftime('%I').lstrip('0')}:{start.strftime('%M')} {start.strftime('%p')}"


def format_reminder_offsets(offsets: list[int]) -> str:
    labels = []
    for minutes in offsets:
        if minutes % 1440 == 0:
            amount = minutes // 1440
            labels.append("1 day before" if amount == 1 else f"{amount} days before")
        elif minutes % 60 == 0:
            amount = minutes // 60
            labels.append("1 hour before" if amount == 1 else f"{amount} hours before")
        else:
            labels.append(f"{minutes} minutes before")
    return ", ".join(labels) if labels else "No reminders"


def appointment_icon(appointment_type: str | None) -> str:
    icons = {
        "dental": "🦷",
        "hair": "💇",
        "doctor": "🩺",
        "gym": "🏋️",
        "school": "🎓",
    }
    return icons.get(appointment_type or "", "📌")


def event_confirmation_text(event: dict, fallback_title: str, draft: dict) -> str:
    title = event.get("summary", fallback_title)
    lines = ["✅ Event added", "", f"{appointment_icon(draft.get('appointment_type'))} {title}"]
    if draft.get("date"):
        lines.append(f"🗓️ {format_local_event_date(draft['date'])}")
    if draft.get("time"):
        start_time = format_local_clock(draft["time"])
        end_dt = datetime.fromisoformat(f"{draft['date']}T{draft['time']}") + timedelta(
            minutes=int(draft.get("duration_minutes", 60))
        )
        lines.append(f"⏰ {start_time} - {format_local_clock(end_dt.strftime('%H:%M'))}")
    if draft.get("location"):
        lines.append(f"📍 {draft['location']}")
    lines.append(f"🔔 {format_reminder_offsets(list(draft.get('reminder_offsets', [])))}")
    return "\n\n".join(lines)


def parse_duration_minutes(value: str) -> int | None:
    text = value.strip().lower()
    fine_match = re.search(r"\b(one|two|three|four|five|\d+(?:\.\d+)?)\s+hours?\s+is\s+fine\b", text)
    if fine_match:
        amount_text = fine_match.group(1)
        amount = number_text_to_int(amount_text)
        if amount is None:
            amount = int(float(amount_text))
        return max(1, int(amount * 60))

    hour_match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b", text)
    if hour_match:
        return max(1, int(float(hour_match.group(1)) * 60))

    word_hour_match = re.search(r"\b(one|two|three|four|five)\s+hours?\b", text)
    if word_hour_match:
        return int(number_text_to_int(word_hour_match.group(1)) * 60)

    minute_match = re.search(r"\b(\d{1,3})\s*(?:minutes?|mins?|m)\b", text)
    if minute_match:
        return max(1, int(minute_match.group(1)))

    if "half hour" in text:
        return 30
    if "hour" in text:
        return 60
    return None


def parse_event_duration_minutes(value: str) -> int | None:
    text = value.strip().lower()
    text = re.sub(r"\bmins?\b", "minutes", text)
    text = re.sub(r"\bhrs?\b", "hours", text)
    patterns = [
        r"\bfor\s+(?P<count>\d+(?:\.\d+)?|one|two|three|four|five)\s*(?P<unit>minutes?|hours?)\b",
        r"\b(?:lasts?|take|takes)\s+(?P<count>\d+(?:\.\d+)?|one|two|three|four|five)\s*(?P<unit>minutes?|hours?)\b",
        r"\b(?P<count>\d+(?:\.\d+)?|one|two|three|four|five)\s*(?P<unit>minutes?|hours?)\s+is\s+fine\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        count_text = match.group("count")
        count = number_text_to_int(count_text)
        if count is None:
            count = float(count_text)
        unit = match.group("unit")
        if unit.startswith("hour"):
            return max(1, int(count * 60))
        return max(1, int(count))
    return None


def number_text_to_int(value: str) -> int | None:
    lowered = value.strip().lower()
    if lowered.isdigit():
        return int(lowered)
    return SMALL_NUMBER_WORDS.get(lowered)


def parse_reminder_offsets(value: str) -> tuple[list[int], bool]:
    text = value.strip().lower()
    if is_no_reminder_reply(value):
        return [], False

    normalized = re.sub(r"\bmins?\b", "minutes", text)
    normalized = re.sub(r"\bhrs?\b", "hours", normalized)
    normalized = re.sub(
        r"\bfor\s+(?:\d+(?:\.\d+)?|one|two|three|four|five|a|an)\s+(?:minutes?|hours?)\b",
        "",
        normalized,
    )
    offsets: list[int] = []

    pattern = re.compile(
        r"\b(?P<count>\d+|one|two|three|four|five|a|an)\s+"
        r"(?P<unit>minutes?|hours?|days?)\b"
    )
    for match in pattern.finditer(normalized):
        count = number_text_to_int(match.group("count"))
        if count is None:
            continue
        unit = match.group("unit")
        if unit.startswith("minute"):
            minutes = count
        elif unit.startswith("hour"):
            minutes = count * 60
        elif unit.startswith("day"):
            minutes = count * 24 * 60
        else:
            continue
        if minutes not in offsets:
            offsets.append(minutes)

    bare_units = (
        ("day before", 24 * 60),
        ("hour before", 60),
    )
    for phrase, minutes in bare_units:
        if phrase in normalized and minutes not in offsets:
            offsets.append(minutes)

    return offsets[:5], len(offsets) > 5


def parse_requested_reminder_count(value: str) -> int | None:
    text = value.strip().lower()
    match = re.search(r"\b(\d+|one|two|three|four|five)\b", text)
    if not match:
        return None
    count = number_text_to_int(match.group(1))
    if count is None:
        return None
    return max(1, min(count, 5))


def reminder_count_label(count: int) -> str:
    labels = {
        1: "one",
        2: "two",
        3: "three",
        4: "four",
        5: "five",
    }
    return labels.get(count, str(count))


def parse_days_from_text(value: str, default: int = 7) -> int:
    lowered = value.lower()
    digit_match = re.search(r"\b(\d{1,2})\s+days?\b", lowered)
    if digit_match:
        return max(1, min(int(digit_match.group(1)), 60))

    for word, number in NUMBER_WORDS.items():
        if re.search(rf"\b{word}\s+days?\b", lowered):
            return max(1, min(number, 60))

    if re.search(r"\bweek\b", lowered):
        return 7

    return default


def asks_for_calendar_range(value: str) -> bool:
    lowered = value.lower()
    has_day_count = bool(re.search(r"\b\d{1,2}\s+days?\b", lowered))
    has_day_word = any(re.search(rf"\b{word}\s+days?\b", lowered) for word in NUMBER_WORDS)
    has_week = bool(re.search(r"\bweek\b", lowered))
    has_calendar_word = any(
        word in lowered
        for word in ("appointment", "appointments", "meeting", "meetings", "calendar", "schedule")
    )
    return (has_day_count or has_day_word or has_week) and has_calendar_word


def is_schedule_intent(value: str) -> bool:
    lowered = value.lower()
    phrases = (
        "schedule",
        "create appointment",
        "new appointment",
        "add appointment",
        "add to calendar",
        "put on my calendar",
        "book",
        "set up",
        "create a call",
        "add a call",
        "calendar event",
    )
    return any(phrase in lowered for phrase in phrases)


def extract_event_request(value: str) -> dict | None:
    if not is_schedule_intent(value):
        return None

    slots = extract_schedule_slots(value)

    match = re.search(
        r"\b(?:create|schedule|add|book|put)\s+(?P<title>.+?)\s+(?:for|on)\s+(?P<date>.+)$",
        value,
        flags=re.IGNORECASE,
    )

    if match:
        title = title_case_event(match.group("title"))
        date_phrase = match.group("date").strip()
    else:
        title_text = re.sub(
            r"\b(can you|please|could you|would you|i need to|i want to)?\s*"
            r"(schedule|create|add|book|set up)\s+(a|an|new)?\s*",
            "",
            value,
            flags=re.IGNORECASE,
        )
        title_text = re.sub(r"\?$", "", title_text).strip()
        title = title_case_event(title_text) if appointment_type_from_text(title_text) else ""
        date_phrase = value
    appointment_type = slots.get("appointment_type") or appointment_type_from_text(title or value)
    if appointment_type and re.search(r"\b(today|tomorrow|next|at\s+\d|\d{1,2}:\d{2}|am|pm|for\s+\d|for\s+one|remind)\b", title, re.IGNORECASE):
        title = f"{appointment_type.title()} appointment"
    if not title and appointment_type:
        title = f"{appointment_type.title()} appointment"

    return {
        "title": title,
        "appointment_type": appointment_type,
        "date": slots.get("date") or parse_human_date(date_phrase),
        "time": slots.get("time") or parse_human_time(date_phrase),
        "duration_minutes": slots.get("duration_minutes") or parse_duration_minutes(value),
        "location": slots.get("location", ""),
        "location_skipped": slots.get("location_skipped", False),
        "reminder_offsets": slots.get("reminder_offsets"),
        "wants_reminders": slots.get("wants_reminders"),
        "reminder_count": slots.get("reminder_count"),
    }


def extract_schedule_slots(value: str, draft: dict | None = None) -> dict:
    draft = draft or {}
    current_step = draft.get("step")
    slots: dict = {}
    appointment_type = appointment_type_from_text(value)
    if appointment_type:
        slots["appointment_type"] = appointment_type
        if not draft.get("title"):
            slots["title"] = f"{appointment_type.title()} appointment"

    date = parse_human_date(value)
    if date:
        slots["date"] = date

    time = parse_human_time(value)
    if time:
        slots["time"] = time

    duration = parse_event_duration_minutes(value)
    if duration:
        slots["duration_minutes"] = duration

    memory_fact = parse_memory_fact(value)
    if memory_fact:
        fact_type, location = memory_fact
        slots["appointment_type"] = slots.get("appointment_type") or fact_type
        slots["location"] = location
        slots["save_location_type"] = fact_type
    elif re.search(r"\b(virtual|at\s+.+)", value, flags=re.IGNORECASE):
        location = extract_location_from_text(value)
        fact_type = None
        if not location and current_step == "location":
            location, _, fact_type = extract_location_reply(value, draft.get("appointment_type") or appointment_type)
        if location and not re.search(r"\b(reminder|remind|minutes?|hours?|days?|tomorrow|today|next)\b", location, re.IGNORECASE):
            slots["location"] = location
            if fact_type:
                slots["save_location_type"] = fact_type
    elif is_skip_location_reply(value):
        slots["location"] = ""
        slots["location_skipped"] = True

    reminder_context = current_step in {"reminders", "reminder_times", "reminder_count"} or re.search(
        r"\b(remind|reminder|reminders)\b", value, flags=re.IGNORECASE
    )
    if is_no_reminder_reply(value) and reminder_context:
        slots["reminder_offsets"] = []
        slots["wants_reminders"] = False
        slots["reminder_count"] = "0"
    else:
        offsets, was_limited = parse_reminder_offsets(value)
        if offsets:
            slots["reminder_offsets"] = offsets
            slots["wants_reminders"] = True
            slots["reminder_count"] = str(len(offsets))
            slots["reminders_limited"] = was_limited

    if is_skip_description_reply(value):
        slots["description"] = ""
    return slots


def extract_location_from_text(value: str) -> str:
    if re.search(r"\bvirtual\b", value, flags=re.IGNORECASE):
        return "Virtual"

    candidates = []
    for match in re.finditer(r"\bat\s+(?!\d)(?P<location>[A-Za-z][^.,]*?)(?=\s*(?:\.|,|$|\bremind\b))", value, flags=re.IGNORECASE):
        location = clean_location_text(match.group("location"))
        if location:
            candidates.append(location)
    return candidates[-1] if candidates else ""


def merge_schedule_slots(draft: dict, slots: dict) -> list[str]:
    notes: list[str] = []
    for key in ("title", "appointment_type", "date", "time", "duration_minutes"):
        if slots.get(key) and not draft.get(key):
            draft[key] = slots[key]

    if "location" in slots and not draft.get("location"):
        draft["location"] = slots["location"]
        draft["location_skipped"] = not bool(slots["location"])

    if slots.get("save_location_type") and slots.get("location"):
        appointment_type = slots["save_location_type"]
        update_appointment_default(appointment_type, "location", slots["location"])
        notes.append(f"Using {slots['location']} as your usual {appointment_type} location.")

    if slots.get("duration_minutes") and draft.get("appointment_type"):
        update_appointment_default(str(draft.get("appointment_type")), "duration_minutes", slots["duration_minutes"])

    if "reminder_offsets" in slots and "reminder_offsets" not in draft:
        draft["reminder_offsets"] = slots["reminder_offsets"]
        draft["wants_reminders"] = bool(slots["reminder_offsets"])
        draft["reminder_count"] = str(len(slots["reminder_offsets"]))
        if draft.get("appointment_type"):
            update_appointment_default(str(draft.get("appointment_type")), "reminder_offsets", slots["reminder_offsets"])
        if slots.get("reminders_limited"):
            notes.append("I can use up to 5 reminders, so I kept the first 5.")

    if "description" in slots and "description" not in draft:
        draft["description"] = slots["description"]

    return notes


def apply_saved_defaults(draft: dict) -> list[str]:
    notes = []
    defaults = appointment_defaults(draft.get("appointment_type"))
    if not defaults:
        return notes

    default_location = usual_location_for_type(draft.get("appointment_type"))
    if not draft.get("location") and default_location:
        draft["location"] = default_location
        draft["location_from_default"] = True
        notes.append(f"Using {default_location} as your usual {draft['appointment_type']} location.")

    if not draft.get("duration_minutes") and defaults.get("duration_minutes"):
        draft["duration_minutes"] = int(defaults["duration_minutes"])
        draft["duration_from_default"] = True

    if "reminder_offsets" not in draft and defaults.get("reminder_offsets") is not None:
        draft["reminder_offsets"] = list(defaults["reminder_offsets"])[:5]
        draft["wants_reminders"] = bool(draft["reminder_offsets"])
        draft["reminder_count"] = str(len(draft["reminder_offsets"]))
        draft["reminders_from_default"] = True

    return notes


def next_event_step(draft: dict) -> str:
    if not draft.get("title"):
        return "title"
    if not draft.get("date"):
        return "date"
    if not draft.get("time"):
        return "time"
    if not draft.get("duration_minutes"):
        return "duration"
    if not draft.get("location") and not draft.get("location_skipped"):
        return "location"
    if "reminder_offsets" not in draft:
        return "reminders"
    return "description"


def step_question(step: str) -> str:
    questions = {
        "title": "Sure. What should I call the appointment?",
        "date": "What date should that be?",
        "time": "What time should it start?",
        "duration": "How long should it be?",
        "location": "Any location for it?",
        "reminders": "Do you want any reminders?",
        "description": "Any description you want to add?",
    }
    return questions.get(step, "What else should I know?")


def reset_field_retries(draft: dict, step: str) -> None:
    draft.setdefault("retries", {})[step] = 0


def retry_prompt(draft: dict, step: str, default_message: str, clearer_message: str) -> str:
    retries = draft.setdefault("retries", {})
    retries[step] = retries.get(step, 0) + 1
    return clearer_message if retries[step] >= 2 else default_message


def parse_reminder_choice(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"yes", "y", "true", "1"}:
        return True
    if normalized in {"no", "n", "false", "0", "none"}:
        return False
    raise ValueError("Reminder choice must be yes or no.")


def build_event_reminders(wants_reminders: bool, count_text: str) -> dict:
    if not wants_reminders:
        return {"useDefault": False, "overrides": []}

    try:
        count = int(count_text.strip())
    except ValueError as e:
        raise ValueError("Reminder count must be a number.") from e

    count = max(1, min(count, len(REMINDER_MINUTES)))
    return {
        "useDefault": False,
        "overrides": [
            {"method": "popup", "minutes": minutes}
            for minutes in REMINDER_MINUTES[:count]
        ],
    }


def build_event_reminders_from_offsets(offsets: list[int]) -> dict:
    return {
        "useDefault": False,
        "overrides": [
            {"method": "popup", "minutes": minutes}
            for minutes in offsets[:5]
        ],
    }


def create_calendar_event(
    title: str,
    date_text: str,
    wants_reminders: bool,
    reminder_count_text: str,
    description: str = "",
    duration_minutes: int = 60,
    location: str = "",
    reminder_offsets: list[int] | None = None,
) -> dict:
    service = get_calendar_service()
    start, end = parse_calendar_date(date_text)
    if "dateTime" in start:
        start_dt = datetime.fromisoformat(start["dateTime"])
        end_dt = start_dt + timedelta(minutes=max(1, duration_minutes))
        end["dateTime"] = end_dt.isoformat()

    event = {
        "summary": title.strip(),
        "start": start,
        "end": end,
        "reminders": (
            build_event_reminders_from_offsets(reminder_offsets)
            if reminder_offsets is not None
            else build_event_reminders(wants_reminders, reminder_count_text)
        ),
    }
    if description.strip():
        event["description"] = description.strip()
    if location.strip():
        event["location"] = location.strip()

    return (
        service.events()
        .insert(calendarId=GOOGLE_CALENDAR_ID, body=event)
        .execute()
    )


def sample_calendar_event_specs(base_date=None) -> list[dict]:
    if base_date is None:
        base_date = datetime.now(LOCAL_TZ).date()
    return [
        {
            "title": "[TEST] Dentist appointment",
            "date_text": f"{(base_date + timedelta(days=1)).isoformat()} 19:30",
            "duration_minutes": 60,
            "location": "Scottsdale Dental",
            "reminder_offsets": [30, 1440],
            "description": "Bubbles scheduling QA sample event.",
        },
        {
            "title": "[TEST] Virtual bingo",
            "date_text": f"{(base_date + timedelta(days=2)).isoformat()} 18:00",
            "duration_minutes": 120,
            "location": "Virtual",
            "reminder_offsets": [15],
            "description": "Bubbles scheduling QA sample event.",
        },
        {
            "title": "[TEST] Haircut appointment",
            "date_text": f"{(base_date + timedelta(days=3)).isoformat()} 14:00",
            "duration_minutes": 45,
            "location": "",
            "reminder_offsets": [],
            "description": "Bubbles scheduling QA sample event.",
        },
    ]


def seed_sample_calendar_events() -> dict:
    specs = sample_calendar_event_specs()
    existing_titles = {
        event.get("summary", "").strip()
        for event in list_calendar_events(days=90, max_results=25)
    }
    created = []
    skipped = []
    for spec in specs:
        if spec["title"] in existing_titles:
            skipped.append(spec["title"])
            continue
        event = create_calendar_event(
            spec["title"],
            spec["date_text"],
            bool(spec["reminder_offsets"]),
            str(len(spec["reminder_offsets"])),
            spec["description"],
            spec["duration_minutes"],
            spec["location"],
            spec["reminder_offsets"],
        )
        created.append(event.get("summary", spec["title"]))
    return {"created": created, "skipped": skipped}


def sample_calendar_seed_summary(result: dict) -> str:
    created = result.get("created", [])
    skipped = result.get("skipped", [])
    lines = ["✅ Sample calendar seed complete"]
    lines.append("")
    lines.append("Created:")
    lines.extend(f"- {title}" for title in created)
    if not created:
        lines.append("- None")
    lines.append("")
    lines.append("Skipped existing:")
    lines.extend(f"- {title}" for title in skipped)
    if not skipped:
        lines.append("- None")
    return "\n".join(lines)


def calendar_summary(days: int = 7) -> str:
    events = list_calendar_events(days=days)
    return f"📅 Upcoming calendar events ({days} days)\n\n{format_calendar_events(events)}"


def next_appointment_summary() -> str:
    events = list_calendar_events(days=60, max_results=1)
    return "📅 Next appointment\n\n" + format_calendar_events(events, "No upcoming appointments found.")


def next_available_day_summary(days: int = 14) -> str:
    events = list_calendar_events(days=days, max_results=25)
    busy_dates = {event_start_text(event)[:10] for event in events if event_start_text(event) != "unknown time"}
    today = datetime.now(timezone.utc).date()

    for offset in range(days):
        candidate = today + timedelta(days=offset)
        if candidate.isoformat() not in busy_dates:
            return f"📅 Next available day\n\n{candidate.isoformat()} has no events on {GOOGLE_CALENDAR_ID}."

    return f"📅 Next available day\n\nNo fully open day found in the next {days} days."


def parse_positive_int(args: list[str], default: int, maximum: int) -> int:
    if not args:
        return default
    try:
        return max(1, min(int(args[0]), maximum))
    except ValueError:
        return default


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    await update.message.reply_text(
        "🤖 Bubbles is online.\n\n"
        "Commands:\n"
        "/id\n"
        "/status\n"
        "/calendar [days]\n"
        "/next\n"
        "/free [days]\n"
        "/add_event <title> | <date> | <reminders yes/no> | <reminder count> | [description]\n"
        "/calendar_setup\n"
        "/ls [path]\n"
        "/read <path>\n"
        "/write <path> | <content>\n\n"
        "You can also ask things like \"What's my next upcoming appointment?\" "
        "or \"Create a call with Sam for the 25th of April.\""
    )


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    user = update.effective_user
    if user is None:
        return

    username = f"@{user.username}" if user.username else "(no username)"
    await update.message.reply_text(
        f"Your Telegram user ID is: {user.id}\nUsername: {username}"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    cpu = run_command(["bash", "-lc", "uptime"])
    ram = run_command(["bash", "-lc", "free -h"])
    disk = run_command(["bash", "-lc", "df -h /"])

    reply = f"🖥️ System Status\n\nUptime:\n{cpu}\n\nRAM:\n{ram}\n\nDisk:\n{disk}"
    await update.message.reply_text(reply[:4000])


async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    days = parse_positive_int(context.args, default=7, maximum=60)
    try:
        await update.message.reply_text(calendar_summary(days)[:4000])
    except Exception as e:
        await update.message.reply_text(f"❌ Calendar error: {e}")


async def next_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    try:
        await update.message.reply_text(next_appointment_summary()[:4000])
    except Exception as e:
        await update.message.reply_text(f"❌ Calendar error: {e}")


async def free_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    days = parse_positive_int(context.args, default=14, maximum=60)
    try:
        await update.message.reply_text(next_available_day_summary(days)[:4000])
    except Exception as e:
        await update.message.reply_text(f"❌ Calendar error: {e}")


async def add_event_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    raw = update.message.text.partition(" ")[2].strip()
    parts = [part.strip() for part in raw.split("|")]
    if len(parts) < 4 or not parts[0] or not parts[1]:
        await update.message.reply_text(
            "Usage: /add_event <title> | <date> | <reminders yes/no> | "
            "<reminder count> | [description]\n\n"
            "Date examples: 2026-04-25 or 2026-04-25 14:30"
        )
        return

    title, date_text, reminder_choice, reminder_count = parts[:4]
    description = parts[4] if len(parts) > 4 else ""

    try:
        wants_reminders = parse_reminder_choice(reminder_choice)
        event = create_calendar_event(
            title,
            date_text,
            wants_reminders,
            reminder_count,
            description,
        )
        await update.message.reply_text(
            "✅ Calendar event added\n\n"
            f"{event_start_text(event)}: {event.get('summary', title)}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Calendar error: {e}")


async def seed_test_events_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return
    if not BUBBLES_ENABLE_DEV_COMMANDS:
        await update.message.reply_text("❌ Dev commands are disabled. Set BUBBLES_ENABLE_DEV_COMMANDS=1 to enable this.")
        return

    try:
        await update.message.reply_text(sample_calendar_seed_summary(seed_sample_calendar_events())[:4000])
    except Exception as e:
        await update.message.reply_text(f"❌ Calendar error: {e}")


async def calendar_setup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    await update.message.reply_text(
        "Google Calendar setup:\n\n"
        "1. Enable the Google Calendar API in Google Cloud.\n"
        "2. Create an OAuth client ID for a Desktop app.\n"
        f"3. Save the downloaded JSON as {GOOGLE_CREDENTIALS_PATH}.\n"
        "4. On this machine, run: python3 bubbles.py --google-auth\n\n"
        "After that, use /calendar, /next, /free, or /add_event."
    )


async def ls_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    path = " ".join(context.args).strip() if context.args else "."
    try:
        items = os.listdir(path)
        if not items:
            await update.message.reply_text("Folder is empty.")
            return
        await update.message.reply_text("\n".join(items)[:4000])
    except Exception as e:
        await update.message.reply_text(f"❌ Error reading folder: {e}")


async def read_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /read <path>")
        return

    path = " ".join(context.args).strip()
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        await update.message.reply_text(content[:4000] if content else "(empty file)")
    except Exception as e:
        await update.message.reply_text(f"❌ Error reading file: {e}")


async def write_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    raw = update.message.text[len("/write "):].strip()
    if "|" not in raw:
        await update.message.reply_text("Usage: /write <path> | <content>")
        return

    path, content = raw.split("|", 1)
    path = path.strip()
    content = content.lstrip()

    try:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        await update.message.reply_text(f"✅ Wrote to {path}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error writing file: {e}")


async def send_text(update: Update, text: str, remember: bool = True) -> None:
    await update.message.reply_text(text[:4000])
    user = update.effective_user
    if remember and user is not None:
        remember_message(user.id, "assistant", text[:4000])


async def ask_next_event_question(update: Update, draft: dict, notes: list[str] | None = None) -> None:
    user = update.effective_user
    if user is None:
        return
    step = next_event_step(draft)
    draft["step"] = step
    reset_field_retries(draft, step)
    PENDING_EVENTS[user.id] = draft
    prefix = " ".join(notes or [])
    question = step_question(step)
    await send_text(update, f"{prefix} {question}".strip())


async def finalize_event_draft(update: Update, draft: dict, description: str = "") -> None:
    user = update.effective_user
    date_text = f"{draft['date']} {draft['time']}"
    event = create_calendar_event(
        draft["title"],
        date_text,
        bool(draft.get("wants_reminders")),
        str(draft.get("reminder_count", "0")),
        description,
        int(draft.get("duration_minutes", 60)),
        str(draft.get("location", "")),
        list(draft.get("reminder_offsets", [])),
    )
    if user is not None:
        PENDING_EVENTS.pop(user.id, None)
    await send_text(update, event_confirmation_text(event, draft["title"], draft))


async def continue_event_draft(update: Update, user_input: str) -> bool:
    user = update.effective_user
    if user is None or user.id not in PENDING_EVENTS:
        return False

    lowered = normalize_reply(user_input)
    if lowered in {"cancel", "stop", "never mind", "nevermind"}:
        PENDING_EVENTS.pop(user.id, None)
        await send_text(update, "Canceled the calendar event.")
        return True

    draft = PENDING_EVENTS[user.id]
    step = draft.get("step")

    try:
        slots = extract_schedule_slots(user_input, draft)
        notes = merge_schedule_slots(draft, slots)
        next_step = next_event_step(draft)
        keep_specialized_reminder_step = step in {"reminder_count", "reminder_times"} and next_step == "reminders"
        if next_step != step and not keep_specialized_reminder_step:
            if next_step == "description":
                await finalize_event_draft(update, draft, str(draft.get("description", "")))
                return True
            await ask_next_event_question(update, draft, notes)
            return True

        if step == "title":
            title = title_case_event(user_input)
            if not title:
                await send_text(update, retry_prompt(draft, step, "What should I call the appointment?", "Please send a short title, like Bingo or Dentist appointment."))
                return True
            draft["title"] = title
            draft["appointment_type"] = appointment_type_from_text(title)
            notes = apply_saved_defaults(draft)
            await ask_next_event_question(update, draft, notes)
            return True

        if step == "date":
            parsed_date = parse_human_date(user_input)
            if not parsed_date:
                await send_text(update, retry_prompt(draft, step, "What date should that be? For example: tomorrow, next Friday, or April 25.", "Please send a date like tomorrow, next Friday, or April 25."))
                return True
            draft["date"] = parsed_date
            await ask_next_event_question(update, draft)
            return True

        if step == "time":
            parsed_time = parse_human_time(user_input)
            if not parsed_time:
                await send_text(update, retry_prompt(draft, step, "What time should it start? For example: 2pm or 14:30.", "Please send a start time like 6 p.m., 2pm, or 14:30."))
                return True
            draft["time"] = parsed_time
            await ask_next_event_question(update, draft)
            return True

        if step == "duration":
            duration = parse_duration_minutes(user_input)
            if duration is None:
                await send_text(update, retry_prompt(draft, step, "How long should it be? For example: 30 minutes or 1 hour.", "Please send a duration like 30 minutes or 2 hours."))
                return True
            draft["duration_minutes"] = duration
            update_appointment_default(str(draft.get("appointment_type", "")), "duration_minutes", duration)
            await ask_next_event_question(update, draft)
            return True

        if step == "location":
            location, should_save, fact_type = extract_location_reply(user_input, draft.get("appointment_type"))
            if lowered in {"same as usual", "usual", "normal location", "use my usual location"} and not location:
                await send_text(update, retry_prompt(draft, step, "I don't have a usual location saved for this yet. What's the location?", "Send a location like Virtual or Scottsdale Dental, or say skip."))
                return True

            if fact_type and not draft.get("appointment_type"):
                draft["appointment_type"] = fact_type
            appointment_type = str(fact_type or draft.get("appointment_type", ""))
            draft["location"] = location
            draft["location_skipped"] = not bool(location)
            if location and (should_save or appointment_type):
                update_appointment_default(appointment_type, "location", location)

            note = []
            if should_save and location and appointment_type:
                note.append(f"Using {location} as your usual {appointment_type} location.")
            await ask_next_event_question(update, draft, note)
            return True

        if step == "reminders":
            if lowered in {"same as usual", "use my normal reminders", "normal reminders", "usual reminders"}:
                defaults = appointment_defaults(draft.get("appointment_type"))
                if defaults.get("reminder_offsets") is None:
                    await send_text(update, retry_prompt(draft, step, "I don't have usual reminders saved for this yet. When should I remind you?", "Send reminder times like 30 minutes before, or say no reminders."))
                    return True
                draft["reminder_offsets"] = list(defaults.get("reminder_offsets", []))[:5]
                draft["wants_reminders"] = bool(draft["reminder_offsets"])
                draft["reminder_count"] = str(len(draft["reminder_offsets"]))
                await ask_next_event_question(update, draft)
                return True

            offsets, was_limited = parse_reminder_offsets(user_input)
            if offsets:
                draft["reminder_offsets"] = offsets
                draft["wants_reminders"] = True
                draft["reminder_count"] = str(len(offsets))
                update_appointment_default(str(draft.get("appointment_type", "")), "reminder_offsets", offsets)
                suffix = " I can use up to 5, so I kept the first 5." if was_limited else ""
                await ask_next_event_question(update, draft, [f"Got it.{suffix}"])
                return True

            if is_yes_reminder_without_details(user_input):
                draft["wants_reminders"] = True
                draft["step"] = "reminder_times"
                reset_field_retries(draft, "reminder_times")
                await send_text(update, "Sure — when should I remind you?")
                return True

            if is_no_reminder_reply(user_input):
                draft["reminder_offsets"] = []
                draft["wants_reminders"] = False
                draft["reminder_count"] = "0"
                update_appointment_default(str(draft.get("appointment_type", "")), "reminder_offsets", [])
                await ask_next_event_question(update, draft)
                return True

            await send_text(update, retry_prompt(draft, step, "When should I remind you? For example: 30 minutes before, or 1 day before.", "Send reminder times like 10 min before, or say no reminders."))
            return True

        if step == "reminder_times":
            if is_no_reminder_reply(user_input):
                draft["reminder_offsets"] = []
                draft["wants_reminders"] = False
                draft["reminder_count"] = "0"
                update_appointment_default(str(draft.get("appointment_type", "")), "reminder_offsets", [])
                await ask_next_event_question(update, draft)
                return True

            offsets, was_limited = parse_reminder_offsets(user_input)
            if not offsets:
                await send_text(update, retry_prompt(draft, step, "What reminder times should I use? For example: 30 minutes before and 1 day before.", "Send reminder times like 10 min before, or say no reminders."))
                return True
            draft["reminder_offsets"] = offsets
            draft["reminder_count"] = str(len(offsets))
            update_appointment_default(str(draft.get("appointment_type", "")), "reminder_offsets", offsets)
            suffix = " I can use up to 5, so I kept the first 5." if was_limited else ""
            await ask_next_event_question(update, draft, [f"Got it.{suffix}"])
            return True

        if step == "reminder_count":
            if is_no_reminder_reply(user_input):
                draft["reminder_offsets"] = []
                draft["wants_reminders"] = False
                draft["reminder_count"] = "0"
                update_appointment_default(str(draft.get("appointment_type", "")), "reminder_offsets", [])
                await ask_next_event_question(update, draft)
                return True

            offsets, was_limited = parse_reminder_offsets(user_input)
            if offsets:
                draft["reminder_offsets"] = offsets
                draft["wants_reminders"] = True
                draft["reminder_count"] = str(len(offsets))
                update_appointment_default(str(draft.get("appointment_type", "")), "reminder_offsets", offsets)
                suffix = " I can use up to 5, so I kept the first 5." if was_limited else ""
                await ask_next_event_question(update, draft, [f"Got it.{suffix}"])
                return True

            count = parse_requested_reminder_count(user_input)
            if count is None:
                await send_text(update, retry_prompt(draft, step, "What reminder times should I use? For example: 10 minutes and 1 day before.", "Send reminder times like 10 min before, or say no reminders."))
                return True
            draft["wants_reminders"] = True
            draft["requested_reminder_count"] = count
            draft["step"] = "reminder_times"
            label = "reminder time" if count == 1 else "reminder times"
            await send_text(update, f"Got it. What should the {reminder_count_label(count)} {label} be?")
            return True

        if step == "description":
            memory_fact = parse_memory_fact(user_input)
            if memory_fact:
                fact_type, location = memory_fact
                draft["appointment_type"] = draft.get("appointment_type") or fact_type
                draft["location"] = location
                update_appointment_default(fact_type, "location", location)
                description = ""
            elif re.search(r"\bat\s+.+", user_input, flags=re.IGNORECASE) and not re.search(
                r"\b(description|note|notes|details)\b", user_input, flags=re.IGNORECASE
            ):
                location, _, _ = extract_location_reply(user_input, draft.get("appointment_type"))
                if location:
                    draft["location"] = location
                description = ""
            else:
                description = "" if is_skip_description_reply(user_input) else user_input.strip()
            await finalize_event_draft(update, draft, description)
            return True
    except Exception as e:
        PENDING_EVENTS.pop(user.id, None)
        await send_text(update, f"❌ Calendar error: {e}")
        return True

    return False


async def start_event_draft(update: Update, request: dict) -> None:
    user = update.effective_user
    if user is None:
        return

    draft = {
        "title": request["title"],
        "appointment_type": request.get("appointment_type"),
        "date": request.get("date"),
        "time": request.get("time"),
        "duration_minutes": request.get("duration_minutes"),
        "location": request.get("location", ""),
    }
    if request.get("location_skipped"):
        draft["location_skipped"] = True
    if request.get("reminder_offsets") is not None:
        draft["reminder_offsets"] = list(request.get("reminder_offsets", []))
        draft["wants_reminders"] = bool(draft["reminder_offsets"])
        draft["reminder_count"] = str(len(draft["reminder_offsets"]))
    notes = apply_saved_defaults(draft)
    if next_event_step(draft) == "description":
        await finalize_event_draft(update, draft, "")
        return
    await ask_next_event_question(update, draft, notes)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    if not update.message or not update.message.text:
        return

    user = update.effective_user
    if user is None:
        return

    user_input = update.message.text.strip()
    lowered = user_input.lower()

    if user.id in PENDING_EVENTS:
        remember_message(user.id, "user", user_input)

    if await continue_event_draft(update, user_input):
        return

    try:
        memory_fact = parse_memory_fact(user_input)
        if memory_fact:
            appointment_type, location = memory_fact
            update_appointment_default(appointment_type, "location", location)
            remember_message(user.id, "user", user_input)
            await send_text(update, f"Got it — I'll remember {location} for {appointment_type} appointments.")
            return

        event_request = extract_event_request(user_input)
        if event_request:
            remember_message(user.id, "user", user_input)
            await start_event_draft(update, event_request)
            return

        if asks_for_calendar_range(user_input):
            remember_message(user.id, "user", user_input)
            await send_text(update, calendar_summary(parse_days_from_text(user_input)))
            return

        if "next appointment" in lowered or "next meeting" in lowered or "upcoming appointment" in lowered:
            remember_message(user.id, "user", user_input)
            await send_text(update, next_appointment_summary())
            return
        if "next available" in lowered or "available day" in lowered or "opening" in lowered:
            remember_message(user.id, "user", user_input)
            await send_text(update, next_available_day_summary())
            return
        if "calendar" in lowered and ("check" in lowered or "what" in lowered or "show" in lowered):
            remember_message(user.id, "user", user_input)
            await send_text(update, calendar_summary(parse_days_from_text(user_input)))
            return
    except Exception as e:
        remember_message(user.id, "user", user_input)
        await send_text(update, f"❌ Calendar error: {e}")
        return

    reply = ask_ollama(user.id, user_input)
    remember_message(user.id, "user", user_input)
    await send_text(update, reply)


def main():
    if "--google-auth" in sys.argv:
        try:
            print(setup_google_calendar_auth())
        except RuntimeError as e:
            print(f"❌ {e}")
            raise SystemExit(1) from e
        return
    if "--seed-sample-events" in sys.argv:
        try:
            print(sample_calendar_seed_summary(seed_sample_calendar_events()))
        except RuntimeError as e:
            print(f"❌ {e}")
            raise SystemExit(1) from e
        return

    if not BOT_TOKEN:
        raise RuntimeError("Missing BUBBLES_BOT_TOKEN. Add it to .env or export it in the environment.")
    if ALLOWED_USER_ID is None:
        raise RuntimeError("Missing BUBBLES_ALLOWED_USER_ID. Add your Telegram numeric user ID to .env.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("calendar", calendar_command))
    app.add_handler(CommandHandler("next", next_command))
    app.add_handler(CommandHandler("free", free_command))
    app.add_handler(CommandHandler("add_event", add_event_command))
    app.add_handler(CommandHandler("seed_test_events", seed_test_events_command))
    app.add_handler(CommandHandler("calendar_setup", calendar_setup_command))
    app.add_handler(CommandHandler("ls", ls_command))
    app.add_handler(CommandHandler("read", read_command))
    app.add_handler(CommandHandler("write", write_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print(f"🤖 Bubbles (@bubbles_sys_bot) is running with Ollama model {MODEL} at {OLLAMA_URL}.")
    app.run_polling()


if __name__ == "__main__":
    main()
