import asyncio
import json
import os
import platform
import re
import requests
import subprocess
import sys
from base64 import urlsafe_b64decode
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, MessageHandler, CommandHandler, filters, ContextTypes

try:
    from mailman import build_mailman_digest as mailman_build_digest
except Exception as e:
    mailman_build_digest = None
    MAILMAN_IMPORT_ERROR = e
else:
    MAILMAN_IMPORT_ERROR = None


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

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2:3b"
STALE_OLLAMA_MODELS = {"qwen3:8b"}
OLLAMA_MODE = "chat"
OLLAMA_CONNECT_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_CONNECT_TIMEOUT", "5"))
OLLAMA_READ_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_READ_TIMEOUT", "30"))
OLLAMA_COOLDOWN_SECONDS = int(os.getenv("OLLAMA_COOLDOWN_SECONDS", "60"))
OLLAMA_HEALTH_TIMEOUT_SECONDS = 3
OLLAMA_REQUEST_TIMEOUT = (OLLAMA_CONNECT_TIMEOUT_SECONDS, OLLAMA_READ_TIMEOUT_SECONDS)
OLLAMA_MAX_HISTORY_TURNS = int(os.getenv("OLLAMA_MAX_HISTORY_TURNS", "2"))
OLLAMA_HISTORY_LIMIT = OLLAMA_MAX_HISTORY_TURNS * 2
OLLAMA_MESSAGE_CHAR_LIMIT = 750
OLLAMA_PROMPT_CHAR_LIMIT = int(os.getenv("OLLAMA_MAX_PROMPT_CHARS", "1400"))
OLLAMA_FAILURE_COOLDOWN_THRESHOLD = max(1, int(os.getenv("OLLAMA_FAILURE_COOLDOWN_THRESHOLD", "2")))
OLLAMA_COOLING_MESSAGE = "I’m having trouble thinking deeply right now, but I can still help with email, calendar, and status."


def normalize_ollama_base_url(value: str | None) -> str:
    url = (value or DEFAULT_OLLAMA_BASE_URL).strip().rstrip("/")
    if not url:
        return DEFAULT_OLLAMA_BASE_URL
    for suffix in ("/api/chat", "/api/generate"):
        if url.endswith(suffix):
            return url[: -len(suffix)] or DEFAULT_OLLAMA_BASE_URL
    return url


def ollama_endpoint_url(endpoint: str, configured_url: str | None = None, configured_base_url: str | None = None) -> str:
    if endpoint not in {"chat", "generate"}:
        raise ValueError("Ollama endpoint must be chat or generate.")
    base_url = normalize_ollama_base_url(configured_base_url or configured_url)
    return f"{base_url}/api/{endpoint}"


OLLAMA_BASE_URL = normalize_ollama_base_url(os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_URL"))
OLLAMA_CHAT_URL = ollama_endpoint_url("chat", configured_base_url=OLLAMA_BASE_URL)
OLLAMA_GENERATE_URL = ollama_endpoint_url("generate", configured_base_url=OLLAMA_BASE_URL)
OLLAMA_URL = OLLAMA_CHAT_URL


def selected_ollama_model() -> str:
    configured = os.getenv("OLLAMA_MODEL", "").strip()
    if configured and configured not in STALE_OLLAMA_MODELS:
        return configured
    return DEFAULT_OLLAMA_MODEL


MODEL = selected_ollama_model()

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
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GOOGLE_OAUTH_SCOPES = CALENDAR_SCOPES + [GMAIL_MODIFY_SCOPE]
GMAIL_FETCH_LIMIT = max(1, min(int(os.getenv("GMAIL_FETCH_LIMIT", "10")), 25))
GMAIL_QUERY = os.getenv("GMAIL_QUERY", "newer_than:2d")
GMAIL_PROMO_QUERY = os.getenv("GMAIL_PROMO_QUERY", "category:promotions newer_than:7d")
GMAIL_UNREAD_QUERY = os.getenv("GMAIL_UNREAD_QUERY", f"{GMAIL_QUERY} is:unread")
MAILMAN_RECENT_MODE = "recent"
MAILMAN_FULL_UNREAD_MODE = "full_unread"
FULL_UNREAD_QUERY = "is:unread"
PROACTIVE_DIGESTS_ENABLED = os.getenv("BUBBLES_PROACTIVE_DIGESTS", "1").strip().lower() in {"1", "true", "yes", "on"}
MORNING_DIGEST_TIME = os.getenv("BUBBLES_MORNING_DIGEST_TIME", "08:00")
AFTERNOON_DIGEST_TIME = os.getenv("BUBBLES_AFTERNOON_DIGEST_TIME", "13:00")
EVENING_DIGEST_TIME = os.getenv("BUBBLES_EVENING_DIGEST_TIME", "19:00")
DIGEST_CHECK_INTERVAL_SECONDS = max(30, int(os.getenv("BUBBLES_DIGEST_CHECK_SECONDS", "60")))
LOG_PATH = Path(os.getenv("BUBBLES_LOG_PATH", "logs/bubbles.log"))
REMINDER_MINUTES = [10, 30, 60, 24 * 60, 7 * 24 * 60]
PENDING_EVENTS: dict[int, dict] = {}
PENDING_EMAIL_APPOINTMENTS: list[dict] = []
EMAIL_CARDS: dict[int, dict] = {}
NEXT_EMAIL_CARD_ID = 1
SCAN_BATCHES: dict[int, dict] = {}
NEXT_SCAN_BATCH_ID = 1
UNREAD_FEEDS: dict[int, dict] = {}
NEXT_UNREAD_FEED_ID = 1
SHOWN_SCAN_ITEM_IDS_BY_DATE: dict[str, dict[str, set[str]]] = {}
TODAY_IMPORTANT_EMAILS: list[dict] = []
SCANNED_EMAIL_IDS: set[str] = set()
RECENT_ACTIONABLE_ITEMS: list[dict] = []
CONTROLLER_STATE: dict[str, object] = {
    "latest_email_card_id": None,
    "latest_email_item": None,
    "latest_email_choices": [],
    "latest_appointment_index": None,
    "latest_item_type": None,
    "active_scan_batch_id": None,
    "more_source": None,
    "more_available": False,
}
LAST_UNREAD_FLOW_DEBUG: dict[str, object] = {}
CHAT_MEMORY: dict[int, list[dict[str, str]]] = {}
LAST_OLLAMA_HEALTH: dict | None = None
OLLAMA_OFFLINE_UNTIL: float = 0
OLLAMA_LAST_ERROR: str = ""
OLLAMA_CONSECUTIVE_FAILURES = 0
OLLAMA_CONSECUTIVE_TIMEOUTS = 0
SYSTEM_PROMPT = "You are Bubbles, a concise Telegram assistant. Reply briefly."
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


def cap_text(value: str, limit: int) -> str:
    text = value or ""
    if len(text) <= limit:
        return text
    return text[: limit - 15].rstrip() + "\n[truncated]"


def include_in_ollama_history(item: dict[str, str]) -> bool:
    if item.get("role") != "assistant":
        return True
    content = item.get("content", "")
    blocked_markers = (
        "Upcoming calendar events",
        "Next appointment",
        "Next available day",
        "Calendar event added",
        "Calendar error",
        "System Status",
        "Uptime:",
        "RAM:",
        "Disk:",
    )
    return not any(marker in content for marker in blocked_markers)


def utc_iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def log_event(event: str, **fields) -> None:
    safe_fields = {
        key: compact_text(str(value), 160)
        for key, value in fields.items()
        if value not in ("", None) and key not in {"token", "credential", "body", "secret"}
    }
    parts = [utc_iso_now(), event]
    parts.extend(f"{key}={value}" for key, value in safe_fields.items())
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(" ".join(parts) + "\n")
    except OSError:
        pass


def read_log_tail(limit: int = 10) -> list[str]:
    try:
        lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    useful = [line for line in lines if line.strip()]
    return useful[-limit:]


def default_memory() -> dict:
    return {
        "appointment_defaults": {},
        "gmail": {
            "seen": [],
            "shown": [],
            "read": [],
            "unread": [],
            "skipped": [],
            "summarized": [],
            "calendar_added": [],
        },
        "scan": {"last_scan_at": ""},
        "digest_history": [],
    }


def normalize_memory(data: dict) -> dict:
    memory = default_memory()
    if isinstance(data, dict):
        memory.update(data)
    if not isinstance(memory.get("appointment_defaults"), dict):
        memory["appointment_defaults"] = {}
    gmail = memory.get("gmail") if isinstance(memory.get("gmail"), dict) else {}
    normalized_gmail = {}
    for key in ("seen", "shown", "read", "unread", "skipped", "summarized", "calendar_added"):
        values = gmail.get(key, [])
        if isinstance(values, set):
            values = list(values)
        if not isinstance(values, list):
            values = []
        normalized_gmail[key] = sorted({str(value) for value in values if value})
    memory["gmail"] = normalized_gmail
    scan = memory.get("scan") if isinstance(memory.get("scan"), dict) else {}
    memory["scan"] = {"last_scan_at": str(scan.get("last_scan_at", ""))}
    history = memory.get("digest_history", [])
    memory["digest_history"] = history[-60:] if isinstance(history, list) else []
    return memory


def load_memory() -> dict:
    if not MEMORY_PATH.exists():
        return default_memory()
    try:
        data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log_event("memory_recovered", reason="missing_or_broken")
        return default_memory()
    if not isinstance(data, dict):
        return default_memory()
    return normalize_memory(data)


def save_memory(data: dict) -> None:
    MEMORY_PATH.write_text(json.dumps(normalize_memory(data), indent=2, sort_keys=True), encoding="utf-8")


def memory_id_set(section: str) -> set[str]:
    return set(load_memory().get("gmail", {}).get(section, []))


def remember_gmail_id(section: str, message_id: str) -> None:
    if not message_id:
        return
    memory = load_memory()
    gmail = memory.setdefault("gmail", {})
    values = set(gmail.get(section, []))
    values.add(message_id)
    gmail[section] = sorted(values)
    save_memory(memory)


def remember_gmail_ids(section: str, message_ids: list[str]) -> None:
    ids = [message_id for message_id in message_ids if message_id]
    if not ids:
        return
    memory = load_memory()
    gmail = memory.setdefault("gmail", {})
    values = set(gmail.get(section, []))
    values.update(ids)
    gmail[section] = sorted(values)
    save_memory(memory)


def gmail_previously_surfaced_ids() -> set[str]:
    memory = load_memory().get("gmail", {})
    surfaced = set()
    for key in ("shown", "read", "skipped", "summarized", "calendar_added"):
        surfaced.update(memory.get(key, []))
    return surfaced


def update_last_scan_timestamp() -> None:
    memory = load_memory()
    memory.setdefault("scan", {})["last_scan_at"] = utc_iso_now()
    save_memory(memory)


def record_digest_history(label: str, shown_count: int) -> None:
    memory = load_memory()
    history = memory.setdefault("digest_history", [])
    history.append({"at": utc_iso_now(), "label": label, "shown": shown_count})
    memory["digest_history"] = history[-60:]
    save_memory(memory)


def memory_summary_text() -> str:
    memory = load_memory()
    gmail = memory.get("gmail", {})
    return (
        "Bubbles memory\n\n"
        f"Seen emails: {len(gmail.get('seen', []))}\n"
        f"Shown emails: {len(gmail.get('shown', []))}\n"
        f"Skipped: {len(gmail.get('skipped', []))}\n"
        f"Summarized: {len(gmail.get('summarized', []))}\n"
        f"Calendar-added: {len(gmail.get('calendar_added', []))}\n"
        f"Last scan: {memory.get('scan', {}).get('last_scan_at') or '—'}"
    )


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
    for item in CHAT_MEMORY.get(user_id, [])[-OLLAMA_HISTORY_LIMIT:]:
        if not include_in_ollama_history(item):
            continue
        role = item.get("role")
        if role not in {"user", "assistant"}:
            continue
        messages.append({"role": role, "content": cap_text(item.get("content", ""), OLLAMA_MESSAGE_CHAR_LIMIT)})
    messages.append({"role": "user", "content": cap_text(user_input, OLLAMA_MESSAGE_CHAR_LIMIT)})
    return messages


def build_ollama_generate_prompt(user_id: int, user_input: str) -> str:
    lines = [
        "System:",
        SYSTEM_PROMPT,
        "",
        "Conversation:",
    ]
    for item in CHAT_MEMORY.get(user_id, [])[-OLLAMA_HISTORY_LIMIT:]:
        if not include_in_ollama_history(item):
            continue
        role = item.get("role")
        if role not in {"user", "assistant"}:
            continue
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {cap_text(item.get('content', ''), OLLAMA_MESSAGE_CHAR_LIMIT)}")
    lines.extend([f"User: {cap_text(user_input, OLLAMA_MESSAGE_CHAR_LIMIT)}", "Assistant:"])
    return cap_text("\n".join(lines), OLLAMA_PROMPT_CHAR_LIMIT)


def parse_ollama_response(data: dict) -> str:
    message = data.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    response = data.get("response")
    if isinstance(response, str) and response.strip():
        return response.strip()
    return "No response from Ollama."


def post_ollama_chat(user_id: int, user_input: str):
    messages = build_ollama_messages(user_id, user_input)
    return requests.post(
        OLLAMA_CHAT_URL,
        json={
            "model": MODEL,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": 64},
        },
        timeout=OLLAMA_REQUEST_TIMEOUT,
    )


def post_ollama_generate(user_id: int, user_input: str):
    return requests.post(
        OLLAMA_GENERATE_URL,
        json={
            "model": MODEL,
            "prompt": build_ollama_generate_prompt(user_id, user_input),
            "stream": False,
            "options": {"num_predict": 64},
        },
        timeout=OLLAMA_REQUEST_TIMEOUT,
    )


def ollama_model_names(tags_data: dict) -> set[str]:
    models = tags_data.get("models", [])
    if not isinstance(models, list):
        return set()
    names = set()
    for model in models:
        if not isinstance(model, dict):
            continue
        name = model.get("name")
        if isinstance(name, str) and name:
            names.add(name)
    return names


def ollama_check_get_tags(timeout: int | float = OLLAMA_HEALTH_TIMEOUT_SECONDS) -> dict:
    try:
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=timeout)
        status_code = response.status_code
        response.raise_for_status()
        data = response.json()
        return {
            "ok": True,
            "status_code": status_code,
            "models": sorted(ollama_model_names(data)),
        }
    except requests.RequestException as e:
        status_code = getattr(getattr(e, "response", None), "status_code", None)
        return {"ok": False, "status_code": status_code, "error": e.__class__.__name__}
    except ValueError:
        return {"ok": False, "status_code": status_code, "error": "Invalid JSON"}


def ollama_check_generate(timeout: int | float = OLLAMA_HEALTH_TIMEOUT_SECONDS) -> dict:
    try:
        response = requests.post(
            OLLAMA_GENERATE_URL,
            json={"model": MODEL, "prompt": "ping", "stream": False},
            timeout=timeout,
        )
        status_code = response.status_code
        response.raise_for_status()
        return {"ok": True, "status_code": status_code}
    except requests.RequestException as e:
        status_code = getattr(getattr(e, "response", None), "status_code", None)
        if status_code is None and "response" in locals():
            status_code = response.status_code
        return {"ok": False, "status_code": status_code, "error": e.__class__.__name__}


def ollama_check_chat(timeout: int | float = OLLAMA_HEALTH_TIMEOUT_SECONDS) -> dict:
    try:
        response = requests.post(
            OLLAMA_CHAT_URL,
            json={"model": MODEL, "messages": [{"role": "user", "content": "ping"}], "stream": False},
            timeout=timeout,
        )
        status_code = response.status_code
        response.raise_for_status()
        return {"ok": True, "status_code": status_code}
    except requests.RequestException as e:
        status_code = getattr(getattr(e, "response", None), "status_code", None)
        if status_code is None and "response" in locals():
            status_code = response.status_code
        return {"ok": False, "status_code": status_code, "error": e.__class__.__name__}


def ollama_diagnostics(timeout: int | float = OLLAMA_HEALTH_TIMEOUT_SECONDS, include_post: bool = False) -> dict:
    tags = ollama_check_get_tags(timeout)
    model_installed = MODEL in set(tags.get("models", [])) if tags.get("ok") else False
    diagnostics = {
        "base_url": OLLAMA_BASE_URL,
        "model": MODEL,
        "tags": tags,
        "model_installed": model_installed,
    }
    if include_post:
        diagnostics["generate"] = ollama_check_generate(timeout)
        diagnostics["chat"] = ollama_check_chat(timeout)
    return diagnostics


def format_ollama_check_result(result: dict) -> str:
    if result.get("ok"):
        return f"ok ({result.get('status_code')})"
    status_code = result.get("status_code")
    error = result.get("error") or "failed"
    if status_code is not None:
        return f"failed ({status_code}, {error})"
    return f"failed ({error})"


def current_timestamp() -> float:
    return datetime.now(timezone.utc).timestamp()


def ollama_cooldown_remaining(now: float | None = None) -> int:
    current = current_timestamp() if now is None else now
    return max(0, int(OLLAMA_OFFLINE_UNTIL - current))


def ollama_brain_state(now: float | None = None) -> str:
    return "cooldown" if ollama_cooldown_remaining(now) > 0 else "online"


def reset_ollama_state() -> None:
    global OLLAMA_OFFLINE_UNTIL
    global OLLAMA_LAST_ERROR
    global OLLAMA_CONSECUTIVE_FAILURES
    global OLLAMA_CONSECUTIVE_TIMEOUTS
    OLLAMA_OFFLINE_UNTIL = 0
    OLLAMA_LAST_ERROR = ""
    OLLAMA_CONSECUTIVE_FAILURES = 0
    OLLAMA_CONSECUTIVE_TIMEOUTS = 0


def mark_ollama_failure(error_type: str, timed_out: bool = False, now: float | None = None) -> None:
    global OLLAMA_OFFLINE_UNTIL
    global OLLAMA_LAST_ERROR
    global OLLAMA_CONSECUTIVE_FAILURES
    global OLLAMA_CONSECUTIVE_TIMEOUTS
    current = current_timestamp() if now is None else now
    OLLAMA_LAST_ERROR = error_type
    OLLAMA_CONSECUTIVE_FAILURES += 1
    if timed_out:
        OLLAMA_CONSECUTIVE_TIMEOUTS += 1
    if OLLAMA_CONSECUTIVE_FAILURES >= OLLAMA_FAILURE_COOLDOWN_THRESHOLD:
        OLLAMA_OFFLINE_UNTIL = current + OLLAMA_COOLDOWN_SECONDS


def mark_ollama_offline(error_type: str, now: float | None = None) -> None:
    mark_ollama_failure(error_type, True, now)


def mark_ollama_online() -> None:
    global OLLAMA_OFFLINE_UNTIL
    global OLLAMA_CONSECUTIVE_FAILURES
    global OLLAMA_CONSECUTIVE_TIMEOUTS
    OLLAMA_OFFLINE_UNTIL = 0
    OLLAMA_CONSECUTIVE_FAILURES = 0
    OLLAMA_CONSECUTIVE_TIMEOUTS = 0


def print_ollama_diagnostics(diagnostics: dict) -> None:
    print(f"Ollama base URL: {diagnostics['base_url']}")
    print(f"Ollama model: {diagnostics['model']}")
    print(f"Ollama GET /api/tags: {format_ollama_check_result(diagnostics['tags'])}")
    if diagnostics["tags"].get("ok"):
        installed = "yes" if diagnostics["model_installed"] else "no"
        print(f"Ollama model installed: {installed}")
        if not diagnostics["model_installed"]:
            print(f"Model {MODEL} is not installed. Run: ollama pull {MODEL}")
    else:
        print(
            "Ollama is not reachable at this URL. Check whether Bubbles is running on SSH/remote "
            "and whether Ollama is running on that same machine."
        )
    if "generate" in diagnostics:
        print(f"Ollama POST /api/generate: {format_ollama_check_result(diagnostics['generate'])}")
    if "chat" in diagnostics:
        print(f"Ollama POST /api/chat: {format_ollama_check_result(diagnostics['chat'])}")


def service_status_text(diagnostics: dict | None = None) -> str:
    diagnostics = diagnostics or ollama_diagnostics(OLLAMA_HEALTH_TIMEOUT_SECONDS)
    ollama_reachable = "yes" if diagnostics["tags"].get("ok") else "no"
    model_installed = "yes" if diagnostics.get("model_installed") else "no"
    gmail_status = "yes" if gmail_configured() else "no"
    calendar_status = "yes" if calendar_configured() else "no"
    return (
        "Bubbles status\n"
        f"Ollama reachable: {ollama_reachable}\n"
        f"Selected model: {MODEL}\n"
        f"Model installed: {model_installed}\n"
        f"Gmail configured: {gmail_status}\n"
        f"Calendar configured: {calendar_status}"
    )


def configure_ollama_mode(timeout: int | float = OLLAMA_HEALTH_TIMEOUT_SECONDS) -> str:
    global OLLAMA_MODE
    global LAST_OLLAMA_HEALTH
    diagnostics = ollama_diagnostics(timeout)
    LAST_OLLAMA_HEALTH = diagnostics
    print_ollama_diagnostics(diagnostics)
    OLLAMA_MODE = "chat"
    print("Ollama endpoint selection: lazy chat; generate fallback on /api/chat 404.")

    return OLLAMA_MODE


def ask_ollama(user_id: int, user_input: str) -> str:
    global OLLAMA_MODE
    if ollama_cooldown_remaining() > 0:
        return OLLAMA_COOLING_MESSAGE

    try:
        if OLLAMA_MODE == "chat":
            response = post_ollama_chat(user_id, user_input)
            if response.status_code == 404:
                OLLAMA_MODE = "generate"
                print("Ollama /api/chat returned 404 during chat; switching to generate fallback.")
                response = post_ollama_generate(user_id, user_input)
        else:
            response = post_ollama_generate(user_id, user_input)

        response.raise_for_status()
        data = response.json()
        mark_ollama_online()
        return parse_ollama_response(data)
    except requests.Timeout as e:
        mark_ollama_failure(e.__class__.__name__, timed_out=True)
        log_event("ollama_timeout", error=e.__class__.__name__, failures=OLLAMA_CONSECUTIVE_FAILURES)
        if ollama_cooldown_remaining() > 0:
            print(f"Ollama request timed out repeatedly: {e.__class__.__name__}; cooling down for {OLLAMA_COOLDOWN_SECONDS}s.")
        else:
            print(f"Ollama request timed out: {e.__class__.__name__}; failure {OLLAMA_CONSECUTIVE_FAILURES}/{OLLAMA_FAILURE_COOLDOWN_THRESHOLD}.")
        return OLLAMA_COOLING_MESSAGE
    except Exception as e:
        mark_ollama_failure(e.__class__.__name__)
        log_event("ollama_error", error=e.__class__.__name__)
        print(f"Ollama request failed: {e}")
        return OLLAMA_COOLING_MESSAGE


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


def token_scopes(path: Path) -> set[str]:
    try:
        token_data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    scopes = token_data.get("scopes", [])
    if isinstance(scopes, str):
        return set(scopes.split())
    return set(scopes)


def token_has_calendar_scopes(path: Path) -> bool:
    granted_scopes = token_scopes(path)
    return all(scope in granted_scopes for scope in CALENDAR_SCOPES)


def token_has_google_oauth_scopes(path: Path) -> bool:
    granted_scopes = token_scopes(path)
    return all(scope in granted_scopes for scope in GOOGLE_OAUTH_SCOPES)



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
        scopes = GOOGLE_OAUTH_SCOPES if token_has_google_oauth_scopes(GOOGLE_TOKEN_PATH) else CALENDAR_SCOPES
        creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_PATH), scopes)

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

    flow = InstalledAppFlow.from_client_secrets_file(str(GOOGLE_CREDENTIALS_PATH), GOOGLE_OAUTH_SCOPES)
    creds = flow.run_local_server(port=0)
    GOOGLE_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return f"Google Calendar and Gmail modify access authorized. Token saved to {GOOGLE_TOKEN_PATH}."


def token_has_scope(path: Path, scope: str) -> bool:
    return scope in token_scopes(path)


def google_reauth_instructions() -> str:
    return f"Delete {GOOGLE_TOKEN_PATH}, run python3 bubbles.py --google-auth, then complete Google OAuth again."


def token_has_gmail_access(path: Path) -> bool:
    granted_scopes = token_scopes(path)
    return GMAIL_MODIFY_SCOPE in granted_scopes or GMAIL_READONLY_SCOPE in granted_scopes


def token_has_gmail_modify(path: Path) -> bool:
    return token_has_scope(path, GMAIL_MODIFY_SCOPE)


def gmail_configuration_issue() -> str:
    if os.getenv("GMAIL_ACCESS_TOKEN", "").strip() or os.getenv("GMAIL_TOKEN", "").strip():
        return ""
    if not GOOGLE_TOKEN_PATH.exists():
        return (
            f"{GOOGLE_TOKEN_PATH} is missing. Run python3 bubbles.py --google-auth and complete Google OAuth."
        )
    if not token_has_gmail_access(GOOGLE_TOKEN_PATH):
        return (
            f"{GOOGLE_TOKEN_PATH} is present, but Gmail access scope is missing. "
            + google_reauth_instructions()
        )
    if not token_has_gmail_modify(GOOGLE_TOKEN_PATH):
        return (
            f"{GOOGLE_TOKEN_PATH} is present, but Gmail modify scope is missing. "
            "Bubbles needs it to mark messages read. "
            + google_reauth_instructions()
        )
    return ""


def gmail_configured() -> bool:
    return gmail_configuration_issue() == ""


def calendar_configured() -> bool:
    return GOOGLE_TOKEN_PATH.exists() and token_has_calendar_scopes(GOOGLE_TOKEN_PATH)


def gmail_status_text() -> str:
    credentials_exists = "yes" if GOOGLE_CREDENTIALS_PATH.exists() else "no"
    token_exists = "yes" if GOOGLE_TOKEN_PATH.exists() else "no"
    gmail_scope = "yes" if GOOGLE_TOKEN_PATH.exists() and token_has_gmail_access(GOOGLE_TOKEN_PATH) else "no"
    gmail_modify = "yes" if GOOGLE_TOKEN_PATH.exists() and token_has_gmail_modify(GOOGLE_TOKEN_PATH) else "no"
    ready = "yes" if gmail_configured() else "no"
    issue = gmail_configuration_issue()
    text = (
        "Gmail status\n"
        f"credentials.json exists: {credentials_exists}\n"
        f"token.json exists: {token_exists}\n"
        f"Gmail access scope present: {gmail_scope}\n"
        f"Gmail modify scope present: {gmail_modify}\n"
        f"Gmail scanning ready: {ready}"
    )
    if issue:
        text += f"\n\nNext step: {issue}"
    return text


def gmail_access_token() -> str:
    token = os.getenv("GMAIL_ACCESS_TOKEN", "").strip() or os.getenv("GMAIL_TOKEN", "").strip()
    if token:
        return token
    if gmail_configuration_issue():
        return ""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        return ""

    creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_PATH), [GMAIL_MODIFY_SCOPE])
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        GOOGLE_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds.token if creds and creds.valid else ""


def gmail_headers(message: dict) -> dict[str, str]:
    headers = {}
    for header in message.get("payload", {}).get("headers", []):
        name = str(header.get("name", "")).lower()
        if name:
            headers[name] = str(header.get("value", ""))
    return headers


def decode_gmail_body(data: str) -> str:
    if not data:
        return ""
    padding = "=" * (-len(data) % 4)
    try:
        return urlsafe_b64decode(data + padding).decode("utf-8", errors="replace")
    except Exception:
        return ""


def extract_gmail_text(payload: dict) -> str:
    body = decode_gmail_body((payload.get("body", {}) or {}).get("data", ""))
    if payload.get("mimeType") == "text/plain" and body.strip():
        return body
    for part in payload.get("parts", []) or []:
        nested = extract_gmail_text(part)
        if nested.strip():
            return nested
    return body


def gmail_error_summary(response: requests.Response) -> str:
    body = response.text.strip()
    message = ""
    reason = ""
    try:
        data = response.json()
        error = data.get("error", {}) if isinstance(data, dict) else {}
        message = str(error.get("message", ""))
        details = error.get("errors", [])
        if details and isinstance(details, list):
            reason = str(details[0].get("reason", ""))
    except ValueError:
        pass
    short_body = re.sub(r"\s+", " ", body)[:500]
    print(f"Gmail scan HTTP {response.status_code}: {short_body}")
    if response.status_code == 403:
        hint = "Check that the Gmail API is enabled for this Google Cloud project and that this Google account is allowed to use it."
        if "insufficient" in message.lower() or "scope" in message.lower() or "insufficient" in reason.lower():
            hint = google_reauth_instructions()
        elif "disabled" in message.lower() or "not been used" in message.lower() or "api" in message.lower():
            hint = "Enable the Gmail API in Google Cloud for this project, then try /scan again."
        elif "access" in message.lower() or "blocked" in message.lower():
            hint = "Google blocked access for this account or app. Check the OAuth consent screen and test users."
        return f"Gmail refused the request: {message or reason or 'Forbidden'}. {hint}"
    return f"Gmail request failed ({response.status_code}): {message or reason or short_body or 'no response body'}"


def gmail_query_count(query: str) -> tuple[int | None, str | None]:
    gmail_issue = gmail_configuration_issue()
    if gmail_issue:
        return None, f"Gmail is not configured: {gmail_issue}"
    token = gmail_access_token()
    if not token:
        return None, "Gmail is not configured: no valid Gmail access token was available."
    try:
        response = requests.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            headers={"Authorization": f"Bearer {token}"},
            params={"maxResults": 1, "q": query},
            timeout=20,
        )
        if response.status_code >= 400:
            return None, gmail_error_summary(response)
        data = response.json()
        estimate = data.get("resultSizeEstimate")
        if isinstance(estimate, int):
            return estimate, None
        return len(data.get("messages", []) or []), None
    except Exception as e:
        return None, f"Gmail count failed: {e}"


def fetch_gmail_message(message_id: str) -> tuple[dict | None, str | None]:
    if not message_id:
        return None, "I could not find that Gmail message."
    gmail_issue = gmail_configuration_issue()
    if gmail_issue:
        return None, f"Gmail is not configured: {gmail_issue}"

    token = gmail_access_token()
    if not token:
        return None, "Gmail is not configured: token.json is present, but no valid Gmail access token was available."

    try:
        detail = requests.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"format": "full"},
            timeout=20,
        )
        if detail.status_code >= 400:
            return None, gmail_error_summary(detail)
        data = detail.json()
        headers = gmail_headers(data)
        payload = data.get("payload", {}) or {}
        return (
            {
                "id": message_id,
                "sender": headers.get("from", ""),
                "subject": headers.get("subject", "(no subject)"),
                "received_at": headers.get("date", ""),
                "snippet": data.get("snippet", ""),
                "body": extract_gmail_text(payload).strip()[:4000],
                "label_ids": data.get("labelIds", []) or [],
                "unread": "UNREAD" in (data.get("labelIds", []) or []),
            },
            None,
        )
    except Exception as e:
        return None, f"Could not read Gmail message: {e}"


def fetch_recent_emails(query: str | None = None, skip_seen: bool = True) -> tuple[list[dict], list[str]]:
    gmail_issue = gmail_configuration_issue()
    if gmail_issue:
        return [], [f"Gmail is not configured: {gmail_issue}"]

    token = gmail_access_token()
    if not token:
        return [], ["Gmail is not configured: token.json is present, but no valid Gmail access token was available."]

    try:
        response = requests.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            headers={"Authorization": f"Bearer {token}"},
            params={"maxResults": GMAIL_FETCH_LIMIT, "q": query or GMAIL_QUERY},
            timeout=20,
        )
        if response.status_code >= 400:
            return [], [gmail_error_summary(response)]
        messages = response.json().get("messages", [])
    except Exception as e:
        return [], [f"Gmail scan failed: {e}"]

    emails = []
    errors = []
    for item in messages:
        message_id = item.get("id")
        if not message_id or (skip_seen and message_id in SCANNED_EMAIL_IDS):
            continue
        email_item, error = fetch_gmail_message(message_id)
        if error:
            errors.append(error)
            continue
        emails.append(email_item)
    return emails, errors


def set_gmail_message_unread(message_id: str, unread: bool) -> str:
    if not message_id:
        return "I could not find that Gmail message."
    gmail_issue = gmail_configuration_issue()
    if gmail_issue:
        return f"I can’t update that email yet. {gmail_issue}"
    token = gmail_access_token()
    if not token:
        return "I can’t update that email because no valid Gmail access token is available."
    payload = {"addLabelIds": ["UNREAD"]} if unread else {"removeLabelIds": ["UNREAD"]}
    try:
        response = requests.post(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}/modify",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=20,
        )
        if response.status_code >= 400:
            return gmail_error_summary(response)
    except Exception as e:
        return f"Gmail read state update failed: {e}"
    return "✅ Marked as unread." if unread else "✅ Marked as read."


def mark_gmail_message_read(message_id: str) -> str:
    return set_gmail_message_unread(message_id, False)


def mark_gmail_message_unread(message_id: str) -> str:
    return set_gmail_message_unread(message_id, True)


def email_is_important(email_item: dict) -> bool:
    text = f"{email_item.get('subject', '')} {email_item.get('snippet', '')} {email_item.get('body', '')}".lower()
    keywords = (
        "appointment",
        "meeting",
        "schedule",
        "scheduled",
        "invite",
        "calendar",
        "deadline",
        "due",
        "urgent",
        "action required",
        "please review",
        "interview",
        "call",
    )
    return any(keyword in text for keyword in keywords)


def email_why_it_matters(email_item: dict) -> str:
    text = f"{email_item.get('subject', '')} {email_item.get('snippet', '')} {email_item.get('body', '')}".lower()
    if any(word in text for word in ("urgent", "asap", "action required")):
        return "It looks time-sensitive."
    if any(word in text for word in ("deadline", "due today", "due tomorrow", "please review")):
        return "There may be a deadline or requested action."
    if any(word in text for word in ("appointment", "meeting", "schedule", "calendar", "interview")):
        return "It may affect your calendar."
    if any(word in text for word in ("invoice", "payment", "receipt", "bill")):
        return "It appears payment-related."
    if any(word in text for word in ("security", "password", "sign-in", "login", "verification")):
        return "It may be account or security related."
    return "It matched your important-email signals."


def email_is_promotional(email_item: dict) -> bool:
    text = f"{email_item.get('sender', '')} {email_item.get('subject', '')} {email_item.get('snippet', '')} {email_item.get('body', '')}".lower()
    promo_words = ("sale", "deal", "discount", "off", "coupon", "offer", "promo", "save", "limited time", "expires")
    weak_words = ("unsubscribe", "newsletter", "digest", "sponsored")
    return any(word in text for word in promo_words) and not all(word in text for word in weak_words)


def promo_score(email_item: dict) -> int:
    text = f"{email_item.get('subject', '')} {email_item.get('snippet', '')} {email_item.get('body', '')}".lower()
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


def email_meaning_category(email_item: dict) -> str:
    text = f"{email_item.get('sender', '')} {email_item.get('subject', '')} {email_item.get('snippet', '')} {email_item.get('body', '')}".lower()
    sender = email_item.get("sender", "").lower()
    if any(word in text for word in ("appointment", "meeting", "schedule", "scheduled", "invite", "calendar", "interview")):
        return "appointment"
    if any(word in text for word in ("invoice", "payment", "bill", "receipt", "statement", "balance due", "past due")):
        return "payment"
    if any(word in text for word in ("deadline", "due today", "due tomorrow", "due by", "expires", "final notice")):
        return "deadline"
    if any(word in text for word in ("security", "password", "sign-in", "login", "verification", "2fa", "account alert")):
        return "security"
    if not email_is_promotional(email_item) and sender and not any(word in sender for word in ("noreply", "no-reply", "donotreply", "newsletter")):
        return "personal"
    if email_is_promotional(email_item):
        return "promo"
    return "review"


def deterministic_email_action(email_item: dict, candidate: dict | None = None) -> str:
    if candidate:
        return "Add to calendar"
    category = email_meaning_category(email_item)
    text = f"{email_item.get('subject', '')} {email_item.get('snippet', '')} {email_item.get('body', '')}"
    if category == "payment":
        due_match = re.search(r"\b(?:by|before|due)\s+([A-Z][a-z]+day|today|tomorrow|[A-Z][a-z]+\s+\d{1,2})", text, flags=re.IGNORECASE)
        return f"Pay before {compact_text(due_match.group(1), 40)}" if due_match else "Review payment"
    if category == "deadline":
        return "Handle deadline"
    if category == "security":
        return "Review security alert"
    if category == "personal":
        return "Reply / review"
    if category == "promo":
        return "Review / optional" if promo_score(email_item) >= 3 else "Ignore"
    return "Review / optional"


def ai_email_action(email_item: dict, candidate: dict | None = None) -> str:
    fallback = deterministic_email_action(email_item, candidate)
    if ollama_cooldown_remaining() > 0:
        return fallback
    prompt = (
        "Infer the single best action for this email. Return only 2-5 words, no punctuation. "
        "Examples: Add to calendar, Pay before Friday, Review optional, Reply, Ignore.\n"
        + json.dumps(
            {
                "subject": compact_text(email_item.get("subject"), 100),
                "from": compact_text(email_item.get("sender"), 80),
                "snippet": compact_text(email_item.get("snippet") or email_item.get("body"), 360),
                "appointment_found": bool(candidate),
            },
            ensure_ascii=True,
        )
    )
    action = compact_text(ollama_digest_completion(prompt), 80)
    action = re.sub(r"^[\"'-]+|[\"'.-]+$", "", action).strip()
    if not action or "\n" in action or len(action.split()) > 7:
        return fallback
    return action[:80]


def ollama_digest_completion(prompt: str) -> str:
    if ollama_cooldown_remaining() > 0:
        return ""
    try:
        response = requests.post(
            OLLAMA_GENERATE_URL,
            json={
                "model": MODEL,
                "prompt": cap_text(prompt, 1800),
                "stream": False,
                "options": {"num_predict": 64},
            },
            timeout=OLLAMA_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        mark_ollama_online()
        return parse_ollama_response(data)
    except requests.Timeout as e:
        mark_ollama_failure(e.__class__.__name__, timed_out=True)
        log_event("ollama_timeout", area="digest", error=e.__class__.__name__, failures=OLLAMA_CONSECUTIVE_FAILURES)
        print(f"Ollama digest ranking timed out: {e.__class__.__name__}.")
    except Exception as e:
        mark_ollama_failure(e.__class__.__name__)
        log_event("ollama_error", area="digest", error=e.__class__.__name__)
        print(f"Ollama digest ranking failed: {e}")
    return ""


def extract_ranked_ids(value: str) -> list[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
        if isinstance(data, list):
            return [str(item) for item in data]
        if isinstance(data, dict):
            ids = data.get("ids") or data.get("items")
            if isinstance(ids, list):
                return [str(item) for item in ids]
    except json.JSONDecodeError:
        pass
    return re.findall(r"[A-Za-z]+:[A-Za-z0-9_-]+", value)


def ai_order_email_ids(items: list[dict], purpose: str, limit: int | None = None) -> list[str]:
    if len(items) < 2:
        return [item.get("id", "") for item in items if item.get("id")]
    lines = [
        "Rank these Gmail message ids for a Telegram assistant.",
        f"Purpose: {purpose}.",
        "Return only a JSON array of ids, best first.",
    ]
    for item in items[:8]:
        lines.append(
            "- "
            + json.dumps(
                {
                    "id": item.get("id", ""),
                    "from": compact_text(item.get("sender", ""), 80),
                    "subject": compact_text(item.get("subject", ""), 100),
                    "snippet": compact_text(item.get("snippet") or item.get("body"), 180),
                },
                ensure_ascii=True,
            )
        )
    ranked = extract_ranked_ids(ollama_digest_completion("\n".join(lines)))
    allowed = {item.get("id", "") for item in items}
    ordered = [item_id for item_id in ranked if item_id in allowed]
    ordered.extend(item.get("id", "") for item in items if item.get("id", "") not in ordered)
    return ordered[:limit] if limit else ordered


def extract_sender_brand(sender: str) -> str:
    sender = sender.strip()
    match = re.search(r"([^<]+)<", sender)
    if match:
        return match.group(1).strip().strip('"')[:80]
    if "@" in sender:
        domain = sender.split("@", 1)[1].split(">", 1)[0]
        return domain.split(".", 1)[0].replace("-", " ").title()[:80]
    return sender[:80] or "Unknown"


def promo_offer_text(email_item: dict) -> str:
    text = re.sub(r"\s+", " ", f"{email_item.get('subject', '')}. {email_item.get('snippet', '')}")
    percent = re.search(r"\b\d{2,3}%\s+off\b[^.]*", text, flags=re.IGNORECASE)
    if percent:
        return percent.group(0).strip()[:160]
    sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    return sentence[:160] or "Offer details were not clear."


def promo_expiry_text(email_item: dict) -> str:
    text = f"{email_item.get('subject', '')} {email_item.get('snippet', '')} {email_item.get('body', '')}"
    match = re.search(r"\b(?:expires|ends|through|until)\s+([^.\n]{3,80})", text, flags=re.IGNORECASE)
    return match.group(1).strip()[:80] if match else "Not found"


def choose_promo_picks(emails: list[dict]) -> list[dict]:
    scored = [(promo_score(item), item) for item in emails if email_is_promotional(item)]
    scored = [(score, item) for score, item in scored if score >= 3]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    candidates = [item for _, item in scored[:5]]
    ranked_ids = ai_order_email_ids(candidates, "choose genuinely useful promotions", limit=2)
    by_id = {item.get("id", ""): item for item in candidates}
    return [by_id[item_id] for item_id in ranked_ids if item_id in by_id][:2]


def short_email_summary(email_item: dict) -> str:
    subject = email_item.get("subject", "(no subject)").strip()
    sender = email_item.get("sender", "").strip()
    snippet = (email_item.get("snippet") or email_item.get("body") or "").strip()
    snippet = re.sub(r"\s+", " ", snippet)[:180]
    prefix = f"{subject}"
    if sender:
        prefix = f"{prefix} from {sender}"
    return f"{prefix}: {snippet}" if snippet else prefix


def fallback_email_summary(email_item: dict) -> tuple[list[str], str]:
    body = compact_text(email_item.get("body") or email_item.get("snippet"), 700)
    if not body:
        return ["No readable body text was available."], "Review / optional"
    sentences = re.split(r"(?<=[.!?])\s+", body)
    points = [compact_text(sentence, 150) for sentence in sentences if sentence.strip()][:3]
    if not points:
        points = [compact_text(body, 150)]
    action_needed = "Review if relevant."
    lowered = body.lower()
    action_match = re.search(r"\b(?:please|kindly|action required|required|need to|must|reply|confirm|schedule)[^.!\n]{0,180}", body, flags=re.IGNORECASE)
    if action_match:
        action_needed = compact_text(action_match.group(0), 120)
    elif any(word in lowered for word in ("meeting", "appointment", "schedule", "calendar", "interview")):
        action_needed = "Check calendar action."
    elif any(word in lowered for word in ("invoice", "payment", "due", "deadline")):
        action_needed = "Check payment or deadline."
    return points, action_needed


def ollama_email_summary(email_item: dict) -> tuple[list[str], str] | None:
    if ollama_cooldown_remaining() > 0:
        return None
    body = compact_text(email_item.get("body") or email_item.get("snippet"), 1800)
    if not body:
        return None
    prompt = (
        "Summarize this email for a Telegram assistant.\n"
        "Return JSON with keys key_points and action. key_points must be 2-3 short strings. action must be one short line.\n"
        + json.dumps(
            {
                "from": email_item.get("sender", ""),
                "subject": email_item.get("subject", ""),
                "body": body,
            },
            ensure_ascii=True,
        )
    )
    raw = ollama_digest_completion(prompt)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [compact_text(raw, 150)], deterministic_email_action(email_item)
    raw_points = data.get("key_points") or data.get("points") or data.get("summary") or []
    if isinstance(raw_points, str):
        points = [compact_text(part.strip(" -"), 150) for part in re.split(r"\n+|(?<=[.!?])\s+", raw_points) if part.strip()]
    elif isinstance(raw_points, list):
        points = [compact_text(str(point), 150) for point in raw_points if str(point).strip()]
    else:
        points = []
    action_needed = compact_text(str(data.get("action") or data.get("action_needed") or ""), 120)
    if not points:
        return None
    return points[:3], action_needed or deterministic_email_action(email_item)


def summarize_email_item(email_item: dict) -> str:
    summary_parts = ollama_email_summary(email_item) or fallback_email_summary(email_item)
    points, action_needed = summary_parts
    bullet_lines = "\n".join(f"- {display_field(point, 150)}" for point in points[:3])
    return (
        "🧠 Email Summary\n\n"
        f"Subject: {display_field(email_item.get('subject') or '(no subject)', 130)}\n"
        f"From: {display_field(email_item.get('sender') or 'Unknown', 130)}\n\n"
        "Key points:\n"
        f"{bullet_lines}\n\n"
        "Action:\n"
        f"{display_field(action_needed, 160)}"
    )


def email_highlight(email_item: dict) -> str:
    text = re.sub(r"\s+", " ", (email_item.get("snippet") or email_item.get("body") or "").strip())
    subject = email_item.get("subject", "").strip()
    combined = f"{subject}. {text}".strip()
    lowered = combined.lower()
    if not combined:
        return "No preview text available."
    if any(word in lowered for word in ("urgent", "immediately", "asap", "action required")):
        prefix = "Urgent: "
    elif any(word in lowered for word in ("invoice", "payment", "paid", "bill", "receipt")):
        prefix = "Payment: "
    elif any(word in lowered for word in ("security", "password", "sign-in", "login", "verification")):
        prefix = "Security: "
    elif any(word in lowered for word in ("deadline", "due", "by tomorrow", "by today")):
        prefix = "Deadline: "
    elif any(word in lowered for word in ("appointment", "meeting", "schedule", "invite", "calendar", "interview")):
        prefix = "Scheduling: "
    else:
        prefix = ""
    sentence = re.split(r"(?<=[.!?])\s+", text or subject, maxsplit=1)[0]
    return (prefix + sentence)[:220]


def email_is_urgent(email_item: dict) -> bool:
    text = f"{email_item.get('subject', '')} {email_item.get('snippet', '')} {email_item.get('body', '')}".lower()
    return any(word in text for word in ("urgent", "asap", "action required", "deadline", "due today", "due tomorrow"))


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
        return match.group(1).strip()
    return ""


def parse_email_received_date(email_item: dict) -> datetime | None:
    value = email_item.get("received_at", "")
    try:
        parsed = parsedate_to_datetime(value)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(LOCAL_TZ)


def detect_email_appointment(email_item: dict) -> dict | None:
    text = f"{email_item.get('subject', '')}\n{email_item.get('snippet', '')}\n{email_item.get('body', '')}"
    lowered = text.lower()
    appointment_words = ("appointment", "meeting", "schedule", "scheduled", "invite", "calendar", "interview", "webinar")
    has_appointment_word = any(word in lowered for word in appointment_words)
    has_time_signal = bool(parse_human_date(text) or parse_human_time(text) or extract_meeting_link(text))
    if not has_appointment_word or not has_time_signal:
        return None

    date_value = parse_human_date(text)
    time_value = parse_human_time(text)
    link = extract_meeting_link(text)
    location = extract_email_location(text)
    if looks_like_time(location):
        location = ""
    title = email_item.get("subject", "").strip() or "Email appointment"
    summary = short_email_summary(email_item)
    return {
        "source_id": email_item.get("id", ""),
        "title": title[:120],
        "date": date_value,
        "time": time_value,
        "location": location[:200],
        "meeting_link": link,
        "sender": email_item.get("sender", ""),
        "status": "pending",
        "description": summary[:1000],
    }


def candidate_date_text(candidate: dict) -> str:
    date_value = (candidate.get("date") or "").strip()
    time_value = (candidate.get("time") or "").strip()
    return f"{date_value} {time_value}".strip()


def validate_email_candidate(candidate: dict) -> str:
    if not candidate.get("title"):
        return "I do not have a clear title for that appointment."
    if not candidate.get("date") or not candidate.get("time"):
        return "I do not have a clear date and start time for that appointment yet."
    try:
        parse_calendar_date(candidate_date_text(candidate))
    except Exception:
        return "I could not parse the appointment start time clearly enough."
    return ""


def missing_candidate_fields(candidate: dict) -> list[str]:
    missing = []
    if not candidate.get("title"):
        missing.append("title")
    if not candidate.get("date"):
        missing.append("date")
    if not candidate.get("time"):
        missing.append("time")
    return missing


SCAN_PAGE_SIZE = 3
UNREAD_EMAIL_CHUNK_SIZE = 3


def next_email_card_id() -> int:
    global NEXT_EMAIL_CARD_ID
    card_id = NEXT_EMAIL_CARD_ID
    NEXT_EMAIL_CARD_ID += 1
    return card_id


def next_scan_batch_id() -> int:
    global NEXT_SCAN_BATCH_ID
    batch_id = NEXT_SCAN_BATCH_ID
    NEXT_SCAN_BATCH_ID += 1
    return batch_id


def next_unread_feed_id() -> int:
    global NEXT_UNREAD_FEED_ID
    feed_id = NEXT_UNREAD_FEED_ID
    NEXT_UNREAD_FEED_ID += 1
    return feed_id


def register_email_card(email_item: dict, kind: str) -> int:
    card_id = next_email_card_id()
    EMAIL_CARDS[card_id] = {
        "id": email_item.get("id", ""),
        "kind": kind,
        "scan_identity": email_scan_identity(email_item.get("id", ""), "promo" if kind == "promo" else "email"),
        "status": "unread" if email_item.get("unread", True) else "read",
        "subject": email_item.get("subject", "(no subject)"),
        "sender": email_item.get("sender", ""),
        "snippet": email_item.get("snippet", ""),
        "body": email_item.get("body", ""),
    }
    return card_id


def controller_context_snapshot() -> dict:
    return {
        "latest_email_card_id": CONTROLLER_STATE.get("latest_email_card_id"),
        "latest_email_item": CONTROLLER_STATE.get("latest_email_item"),
        "latest_email_choices": list(CONTROLLER_STATE.get("latest_email_choices") or []),
        "latest_appointment_index": CONTROLLER_STATE.get("latest_appointment_index"),
        "latest_item_type": CONTROLLER_STATE.get("latest_item_type"),
        "active_scan_batch_id": CONTROLLER_STATE.get("active_scan_batch_id"),
        "more_source": CONTROLLER_STATE.get("more_source"),
        "more_available": bool(CONTROLLER_STATE.get("more_available")),
    }


def controller_remember_email_item(item: dict | None, card_id: int | None = None) -> None:
    CONTROLLER_STATE["latest_email_item"] = item
    CONTROLLER_STATE["latest_email_card_id"] = card_id
    CONTROLLER_STATE["latest_item_type"] = "email"
    if card_id is not None:
        CONTROLLER_STATE["latest_email_choices"] = []


def controller_remember_appointment(index: int | None) -> None:
    CONTROLLER_STATE["latest_appointment_index"] = index
    CONTROLLER_STATE["latest_item_type"] = "appointment"


def controller_set_more(source: str | None, batch_id: int | None, available: bool) -> None:
    CONTROLLER_STATE["more_source"] = source if available else None
    CONTROLLER_STATE["more_available"] = available
    if source == "scan":
        CONTROLLER_STATE["active_scan_batch_id"] = batch_id


def remember_actionable_item(item_type: str, item_id: int, label: str = "") -> None:
    RECENT_ACTIONABLE_ITEMS.append({"type": item_type, "id": item_id, "label": label})
    del RECENT_ACTIONABLE_ITEMS[:-8]
    if item_type == "email":
        controller_remember_email_item(EMAIL_CARDS.get(item_id), item_id)
    if item_type == "appointment":
        controller_remember_appointment(item_id)


def active_email_card_ids() -> list[int]:
    return [
        card_id
        for card_id, card in EMAIL_CARDS.items()
        if card.get("status") in {"read", "unread"}
    ]


def latest_scan_batch_id() -> int | None:
    if not SCAN_BATCHES:
        return None
    return max(SCAN_BATCHES)


def email_card_keyboard(card_id: int) -> InlineKeyboardMarkup:
    card = EMAIL_CARDS.get(card_id, {})
    read_label = "✅ Mark Read" if card.get("status", "unread") != "read" else "📬 Mark Unread"
    read_action = "read" if card.get("status", "unread") != "read" else "unread"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(read_label, callback_data=f"email:{read_action}:{card_id}"),
                InlineKeyboardButton("🧠 Summarize", callback_data=f"email:summarize:{card_id}"),
            ],
            [
                InlineKeyboardButton("⏸ Skip", callback_data=f"email:skip:{card_id}"),
            ],
        ]
    )


def email_status_keyboard(card_id: int, left: str, right: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(left, callback_data=f"email:status:{card_id}"), InlineKeyboardButton(right, callback_data=f"email:status:{card_id}")]])


def appointment_status_keyboard(index: int, left: str, right: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(left, callback_data=f"appt:status:{index}"), InlineKeyboardButton(right, callback_data=f"appt:status:{index}")]])


def scan_more_keyboard(batch_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Show More Emails", callback_data=f"scan:more:{batch_id}")]])


def clean_card_text(lines: list[str]) -> str:
    return "\n" + "\n".join(lines).strip() + "\n"


def compact_text(value: str | None, limit: int = 160) -> str:
    value = (value or "").strip()
    value = re.sub(r"\s+", " ", value)
    if len(value) > limit:
        return value[: max(0, limit - 1)].rstrip() + "…"
    return value


def display_field(value: str | None, limit: int = 160) -> str:
    value = compact_text(value, limit)
    return value if value else "—"


def looks_like_time(value: str) -> bool:
    value = value.strip().lower()
    return bool(re.fullmatch(r"\d{1,2}(:\d{2})?\s*(am|pm)?", value) or re.fullmatch(r"\d{1,2}\s*(am|pm)", value))


def display_location(value: str | None) -> str:
    value = compact_text(value, 140)
    if not value or looks_like_time(value):
        return "—"
    return value


def scan_shown_date_key() -> str:
    return datetime.now(LOCAL_TZ).date().isoformat()


def scan_daily_state() -> dict[str, set[str]]:
    today = scan_shown_date_key()
    for date_key in list(SHOWN_SCAN_ITEM_IDS_BY_DATE):
        if date_key != today:
            SHOWN_SCAN_ITEM_IDS_BY_DATE.pop(date_key, None)
    state = SHOWN_SCAN_ITEM_IDS_BY_DATE.setdefault(today, {})
    if isinstance(state, set):
        state = {"shown": state}
        SHOWN_SCAN_ITEM_IDS_BY_DATE[today] = state
    for key in ("shown", "read", "skipped", "summarized", "added_to_calendar"):
        state.setdefault(key, set())
    return state


def shown_scan_item_ids_today() -> set[str]:
    return scan_daily_state()["shown"]


def scan_item_identity(item: dict) -> str:
    item_type = item.get("type", "email")
    if item_type == "appointment":
        index = item.get("index")
        if isinstance(index, int) and 1 <= index <= len(PENDING_EMAIL_APPOINTMENTS):
            source_id = PENDING_EMAIL_APPOINTMENTS[index - 1].get("source_id", "")
            if source_id:
                return f"appointment:{source_id}"
        return f"appointment:{index}"
    email_item = item.get("email", {})
    message_id = email_item.get("id", "")
    if message_id:
        return f"{item_type}:{message_id}"
    return f"{item_type}:{email_item.get('sender', '')}:{email_item.get('subject', '')}"


def scan_item_message_id(item: dict) -> str:
    if item.get("type") == "appointment":
        index = item.get("index")
        if isinstance(index, int) and 1 <= index <= len(PENDING_EMAIL_APPOINTMENTS):
            return PENDING_EMAIL_APPOINTMENTS[index - 1].get("source_id", "")
        return ""
    return item.get("email", {}).get("id", "")


def scan_item_was_shown_today(item: dict) -> bool:
    return scan_item_processed_today(scan_item_identity(item))


def mark_scan_item_shown_today(item: dict) -> None:
    mark_scan_item_status_today(scan_item_identity(item), "shown")
    message_id = scan_item_message_id(item)
    if message_id:
        remember_gmail_id("shown", message_id)


def scan_item_processed_today(item_id: str) -> bool:
    state = scan_daily_state()
    return any(item_id in values for values in state.values())


def mark_scan_item_status_today(item_id: str, status: str) -> None:
    if item_id:
        scan_daily_state().setdefault(status, set()).add(item_id)


def mark_scan_item_action_today(item: dict, status: str) -> None:
    mark_scan_item_status_today(scan_item_identity(item), status)


def email_scan_identity(message_id: str, kind: str = "email") -> str:
    return f"{kind}:{message_id}" if message_id else ""


def scan_item_text(item: dict) -> str:
    if item.get("type") == "appointment":
        index = item.get("index")
        if isinstance(index, int) and 1 <= index <= len(PENDING_EMAIL_APPOINTMENTS):
            candidate = PENDING_EMAIL_APPOINTMENTS[index - 1]
            return " ".join(
                str(candidate.get(key, ""))
                for key in ("title", "date", "time", "location", "sender", "description")
            ).lower()
        return ""
    email_item = item.get("email", {})
    return " ".join(
        str(email_item.get(key, ""))
        for key in ("sender", "subject", "snippet", "body")
    ).lower()


def deterministic_scan_item_score(item: dict) -> int:
    text = scan_item_text(item)
    score = 0
    if item.get("type") == "appointment":
        score += 120
    elif item.get("type") == "email":
        category = email_meaning_category(item.get("email", {}))
        score += {
            "appointment": 110,
            "payment": 95,
            "deadline": 90,
            "security": 85,
            "personal": 70,
            "review": 45,
            "promo": 20,
        }.get(category, 45)
    elif item.get("type") == "promo":
        score += 20
    score += 30 if any(word in text for word in ("urgent", "asap", "action required", "security", "password")) else 0
    score += 25 if any(word in text for word in ("deadline", "due today", "due tomorrow", "payment", "invoice")) else 0
    score += 20 if any(word in text for word in ("meeting", "appointment", "interview", "calendar")) else 0
    score += min(20, promo_score(item.get("email", {}))) if item.get("type") == "promo" else 0
    return score


def scan_item_ai_summary(item: dict) -> dict:
    item_id = scan_item_identity(item)
    if item.get("type") == "appointment":
        index = item.get("index")
        candidate = PENDING_EMAIL_APPOINTMENTS[index - 1] if isinstance(index, int) and 1 <= index <= len(PENDING_EMAIL_APPOINTMENTS) else {}
        return {
            "id": item_id,
            "type": "appointment",
            "title": compact_text(candidate.get("title"), 100),
            "from": compact_text(candidate.get("sender"), 80),
            "details": compact_text(candidate.get("description"), 160),
        }
    email_item = item.get("email", {})
    return {
        "id": item_id,
        "type": item.get("type", "email"),
        "subject": compact_text(email_item.get("subject"), 100),
        "from": compact_text(email_item.get("sender"), 80),
        "snippet": compact_text(email_item.get("snippet") or email_item.get("body"), 160),
    }


def ai_rank_scan_items(items: list[dict]) -> list[str]:
    if len(items) < 2:
        return [scan_item_identity(item) for item in items]
    lines = [
        "Rank these Telegram digest items by usefulness for an email/calendar assistant.",
        "Priority order: appointments first, then bills/payments, deadlines, security alerts, personal messages, strong promotions last.",
        "Keep similar items in the given order unless meaning clearly changes priority.",
        "Return only a JSON array of ids, best first.",
    ]
    for item in items[:10]:
        lines.append("- " + json.dumps(scan_item_ai_summary(item), ensure_ascii=True))
    ranked = extract_ranked_ids(ollama_digest_completion("\n".join(lines)))
    allowed = {scan_item_identity(item) for item in items}
    return [item_id for item_id in ranked if item_id in allowed]


def rank_scan_items(items: list[dict]) -> list[dict]:
    scored = sorted(
        enumerate(items),
        key=lambda pair: (-deterministic_scan_item_score(pair[1]), scan_item_identity(pair[1]), pair[0]),
    )
    scored_items = [item for _, item in scored]
    ranked_ids = ai_rank_scan_items(scored_items)
    if not ranked_ids:
        return scored_items
    deterministic_position = {scan_item_identity(item): index for index, item in enumerate(scored_items)}
    ai_position = {item_id: index for index, item_id in enumerate(ranked_ids)}
    by_id = {scan_item_identity(item): item for item in scored_items}
    ranked = [by_id[item_id] for item_id in ranked_ids if item_id in by_id]
    ranked.extend(item for item in scored_items if scan_item_identity(item) not in ranked_ids)
    return sorted(
        ranked,
        key=lambda item: (
            -deterministic_scan_item_score(item),
            ai_position.get(scan_item_identity(item), deterministic_position.get(scan_item_identity(item), 999)),
            deterministic_position.get(scan_item_identity(item), 999),
        ),
    )


def format_important_email_card(email_item: dict) -> str:
    lines = [
        "📩 Email",
        "",
        f"Subject: {display_field(email_item.get('subject') or '(no subject)', 130)}",
        f"From: {display_field(email_item.get('sender') or 'Unknown', 130)}",
        f"Action: {display_field(ai_email_action(email_item), 90)}",
        "",
        f"Highlight: {display_field(email_highlight(email_item), 180)}",
    ]
    return clean_card_text(lines)


def format_promo_card(email_item: dict) -> str:
    lines = [
        "📩 Promo Pick",
        "",
        f"Subject: {display_field(email_item.get('subject') or '(no subject)', 130)}",
        f"From: {display_field(extract_sender_brand(email_item.get('sender', '')), 90)}",
        f"Action: {display_field(ai_email_action(email_item), 90)}",
        "",
        f"Offer: {display_field(promo_offer_text(email_item), 150)}",
        f"Expires: {display_field(promo_expiry_text(email_item), 90)}",
    ]
    return clean_card_text(lines)


def format_appointment_candidate(candidate: dict, index: int) -> str:
    lines = ["📅 Appointment"]
    lines.append("")
    lines.append(f"Title: {display_field(candidate.get('title'), 130)}")
    lines.append(f"From: {display_field(candidate.get('sender') or 'Unknown', 130)}")
    lines.append("Action: Add to calendar")
    lines.append("")
    lines.append(f"Date: {display_field(candidate.get('date'), 80)}")
    lines.append(f"Time: {display_field(candidate.get('time'), 60)}")
    lines.append(f"Location: {display_location(candidate.get('location'))}")
    lines.append(f"Link: {display_field(candidate.get('meeting_link'), 180)}")
    if candidate.get("description"):
        lines.append("")
        lines.append(f"Highlight: {display_field(candidate.get('description'), 180)}")
    missing = missing_candidate_fields(candidate)
    if missing:
        lines.append(f"Missing: {', '.join(missing)}")
    return clean_card_text(lines)


def appointment_keyboard(index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Add to Calendar", callback_data=f"appt:add:{index}"),
                InlineKeyboardButton("⏸ Skip", callback_data=f"appt:skip:{index}"),
            ],
        ]
    )


def format_email_card(email_item: dict, candidate: dict | None = None) -> str:
    lines = ["📩 Email"]
    lines.append("")
    lines.append(f"Subject: {display_field(email_item.get('subject') or '(no subject)', 130)}")
    lines.append(f"From: {display_field(email_item.get('sender') or 'Unknown', 130)}")
    lines.append(f"Action: {display_field(ai_email_action(email_item, candidate), 90)}")
    lines.append("")
    lines.append(f"Highlight: {display_field(email_highlight(email_item), 180)}")
    if candidate:
        lines.append(f"Date: {display_field(candidate.get('date'))}")
        lines.append(f"Time: {display_field(candidate.get('time'))}")
        lines.append(f"Location: {display_location(candidate.get('location'))}")
    return clean_card_text(lines)


def pending_email_summary() -> str:
    pending_items = [(index, item) for index, item in enumerate(PENDING_EMAIL_APPOINTMENTS, start=1) if item.get("status", "pending") == "pending"]
    if not pending_items:
        return "No pending email appointments right now."
    lines = ["Pending email appointments"]
    for index, candidate in pending_items:
        lines.append("")
        lines.append(format_appointment_candidate(candidate, index))
    return "\n".join(lines)


def scan_recent_email_for_updates(unread_only: bool = False, persistent_new_only: bool = False) -> dict:
    query = GMAIL_UNREAD_QUERY if unread_only else GMAIL_QUERY
    emails, errors = fetch_recent_emails(query, skip_seen=False)
    promo_query = f"{GMAIL_PROMO_QUERY} is:unread" if unread_only and "is:unread" not in GMAIL_PROMO_QUERY.lower() else GMAIL_PROMO_QUERY
    promo_emails, promo_errors = fetch_recent_emails(promo_query, skip_seen=False)
    errors.extend(promo_errors)
    seen_ids = [item.get("id", "") for item in emails + promo_emails if item.get("id")]
    remember_gmail_ids("seen", seen_ids)
    update_last_scan_timestamp()
    if persistent_new_only:
        surfaced = gmail_previously_surfaced_ids()
        emails = [item for item in emails if item.get("id") not in surfaced]
        promo_emails = [item for item in promo_emails if item.get("id") not in surfaced]
    assistant_categories = {"appointment", "payment", "deadline", "security", "personal"}
    important = [
        item
        for item in emails
        if email_is_important(item) or email_meaning_category(item) in assistant_categories
    ]
    regular_ids = {item.get("id") for item in emails}
    promo_picks = choose_promo_picks([item for item in promo_emails if item.get("id") not in regular_ids])
    candidates = []
    candidate_by_source = {}
    existing_sources = {item.get("source_id") for item in PENDING_EMAIL_APPOINTMENTS}
    for email_item in important:
        candidate = detect_email_appointment(email_item)
        if candidate:
            candidate_by_source[candidate.get("source_id")] = candidate
            if candidate.get("source_id") not in existing_sources:
                PENDING_EMAIL_APPOINTMENTS.append(candidate)
                candidates.append(candidate)
                existing_sources.add(candidate.get("source_id"))
    for email_item in emails + promo_emails:
        if email_item.get("id"):
            SCANNED_EMAIL_IDS.add(email_item["id"])
    TODAY_IMPORTANT_EMAILS[:] = important[:20]
    log_event(
        "emails_found",
        unread_only=unread_only,
        persistent_new_only=persistent_new_only,
        emails=len(emails),
        important=len(important),
        promos=len(promo_picks),
        appointments=len(candidates),
    )
    return {
        "emails": emails,
        "important": important,
        "promo_picks": promo_picks,
        "candidates": candidates,
        "candidate_by_source": candidate_by_source,
        "errors": errors,
    }


def render_scan_result(result: dict) -> str:
    errors = result.get("errors", [])
    important = result.get("important", [])
    candidates = result.get("candidates", [])
    promo_picks = result.get("promo_picks", [])
    lines = ["I checked your inbox."]
    if important or candidates or promo_picks:
        lines.append("")
        lines.append("Here’s what stood out:")
        lines.append(f"• {len(important)} important email{'s' if len(important) != 1 else ''}")
        lines.append(f"• {len(candidates)} appointment item{'s' if len(candidates) != 1 else ''}")
        lines.append(f"• {len(promo_picks)} promo pick{'s' if len(promo_picks) != 1 else ''}")
    if not important and not promo_picks:
        lines.append("")
        lines.append("Nothing new is standing out right now.")
    if errors:
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend(f"• {error}" for error in errors[:3])
    return "\n".join(lines).strip()


def scan_header_text(total: int, start: int, end: int) -> str:
    if total <= 0:
        return "I checked your inbox, but nothing new needs your attention."
    return f"I checked your inbox — here’s what matters.\nShowing {start}–{end} of {total}."


def digest_item_icon(item: dict) -> str:
    if item.get("type") == "appointment":
        return "📅"
    if item.get("type") == "promo":
        return "🎁"
    category = email_meaning_category(item.get("email", {}))
    if category == "payment":
        return "💸"
    if category == "security":
        return "🔐"
    if category == "deadline":
        return "🔥"
    if category == "personal":
        return "👤"
    return "📩"


def digest_item_title_action(item: dict) -> tuple[str, str]:
    if item.get("type") == "appointment":
        index = item.get("index")
        candidate = PENDING_EMAIL_APPOINTMENTS[index - 1] if isinstance(index, int) and 1 <= index <= len(PENDING_EMAIL_APPOINTMENTS) else {}
        title = display_field(candidate.get("title"), 90)
        when = " ".join(part for part in (candidate.get("date", ""), candidate.get("time", "")) if part)
        if when:
            title = f"{title} — {compact_text(when, 40)}"
        return title, "Add to calendar"
    email_item = item.get("email", {})
    title = display_field(email_item.get("subject") or extract_sender_brand(email_item.get("sender", "")), 90)
    return title, ai_email_action(email_item)


def scheduled_digest_intro(label: str, items: list[dict]) -> str:
    greeting = {
        "morning": "🌅 Morning Digest",
        "afternoon": "☀️ Afternoon Digest",
        "evening": "🌙 Evening Digest",
    }.get(label.lower(), f"{label} Digest")
    lines = [
        greeting,
        "",
        f"I found {len(items)} new thing{'s' if len(items) != 1 else ''} worth your attention.",
        "",
    ]
    for index, item in enumerate(items[:3], start=1):
        title, action = digest_item_title_action(item)
        lines.append(f"{index}. {digest_item_icon(item)} {title}")
        lines.append(f"   Action: {display_field(action, 80)}")
        lines.append("")
    lines.append("I’ll show the top cards now.")
    return "\n".join(lines).strip()


def highlights_group(item: dict) -> str:
    if item.get("type") == "appointment":
        return "appointments"
    if item.get("type") == "promo":
        return "promos"
    category = email_meaning_category(item.get("email", {}))
    if category in {"payment", "deadline", "security"}:
        return "urgent" if category in {"deadline", "security"} else "payments"
    if category == "personal":
        return "personal"
    return "urgent" if email_is_urgent(item.get("email", {})) else "personal"


def highlights_item_text(item: dict) -> tuple[str, str]:
    if item.get("type") == "appointment":
        title, _ = digest_item_title_action(item)
        return title, "Calendar action available in /scan."
    email_item = item.get("email", {})
    title = email_item.get("subject") or f"Message from {extract_sender_brand(email_item.get('sender', ''))}"
    if item.get("type") == "promo":
        return compact_text(title, 90), compact_text(promo_offer_text(email_item), 100)
    action = deterministic_email_action(email_item)
    return compact_text(title, 90), compact_text(action if action != "Review / optional" else email_highlight(email_item), 100)


def format_highlights_digest(items: list[dict], result: dict) -> str:
    groups = {
        "urgent": ("🔥 Urgent", []),
        "appointments": ("📅 Appointments", []),
        "payments": ("💸 Bills & Payments", []),
        "personal": ("👤 Personal", []),
        "promos": ("🎁 Promo Picks", []),
    }
    for item in items:
        key = highlights_group(item)
        groups.setdefault(key, (key.title(), []))[1].append(item)

    lines = ["🧠 Inbox Highlights"]
    shown_total = 0
    for _, (heading, group_items) in groups.items():
        if not group_items:
            continue
        lines.append("")
        lines.append(heading)
        for index, item in enumerate(group_items[:2], start=1):
            title, detail = highlights_item_text(item)
            lines.append(f"{index}. {title}")
            lines.append(f"   {detail}")
            shown_total += 1
    if shown_total == 0:
        lines.append("")
        lines.append("No unread highlights stood out in the recent scan.")

    important_count = len(result.get("important", []))
    appointment_count = len(result.get("candidates", []))
    payment_count = sum(1 for item in result.get("important", []) if email_meaning_category(item) == "payment")
    promo_count = len(result.get("promo_picks", []))
    lines.append("")
    lines.append("Summary:")
    lines.append(f"{important_count} important · {appointment_count} appointments · {payment_count} payments · {promo_count} promos")
    return "\n".join(lines)[:4000]


def mailman_summary_line(summary: dict, key: str, singular: str, emoji: str, plural: str | None = None) -> str:
    count = int(summary.get(key, 0) or 0)
    noun = singular if count == 1 else (plural or f"{singular}s")
    return f"{emoji} {count} {noun}"


def render_mailman_digest(digest: dict) -> str:
    log_event("Bubbles rendering Mailman digest")
    summary = digest.get("summary", {}) if isinstance(digest.get("summary"), dict) else {}
    items = digest.get("items", []) if isinstance(digest.get("items"), list) else []
    errors = digest.get("errors", []) if isinstance(digest.get("errors"), list) else []

    lines = [
        "🧠 Mailman Highlights",
        "",
        "📊 Summary",
        f"📩 {int(summary.get('total', 0) or 0)} unread",
        mailman_summary_line(summary, "appointments", "appointment", "📅"),
        mailman_summary_line(summary, "bills", "bill", "💸", "bills"),
        mailman_summary_line(summary, "security", "security", "🔐", "security"),
    ]

    if errors and not items:
        first_error = errors[0] if isinstance(errors[0], dict) else {}
        message = first_error.get("message") if isinstance(first_error, dict) else str(errors[0])
        lines.extend(["", f"Mailman issue: {compact_text(str(message), 220)}"])
        return "\n".join(lines)[:4000]

    if not items:
        lines.extend(["", "No unread highlights stood out."])
        return "\n".join(lines)[:4000]

    lines.extend(["", "🔥 Top Priority", ""])
    for index, item in enumerate(items[:3], start=1):
        if not isinstance(item, dict):
            continue
        title = display_field(item.get("subject"), 90)
        sender = display_field(item.get("from_display") or item.get("from"), 90)
        why = display_field(item.get("why_it_matters"), 120)
        action = display_field(item.get("action"), 90)
        emoji = item.get("emoji") or "📩"
        lines.extend(
            [
                f"{index}. {emoji} {title}",
                f"   From: {sender}",
                f"   Why: {why}",
                f"   Action: {action}",
                "",
            ]
        )

    if lines and lines[-1] == "":
        lines.pop()
    if errors:
        lines.extend(["", f"Note: Mailman reported {len(errors)} account issue{'s' if len(errors) != 1 else ''}."])
    return "\n".join(lines)[:4000]


def call_mailman_digest(
    unread_only: bool = True,
    limit: int | None = None,
    query: str | None = None,
    mode: str = MAILMAN_RECENT_MODE,
) -> dict:
    if mailman_build_digest is None:
        detail = f": {MAILMAN_IMPORT_ERROR}" if MAILMAN_IMPORT_ERROR else ""
        raise RuntimeError(f"Mailman is not available{detail}")
    log_event("Bubbles calling Mailman", mode=mode)
    digest = mailman_build_digest(unread_only=unread_only, limit=limit, query=query, mode=mode)
    items = digest.get("items", []) if isinstance(digest, dict) else []
    item_count = len(items) if isinstance(items, list) else 0
    summary = digest.get("summary", {}) if isinstance(digest, dict) and isinstance(digest.get("summary"), dict) else {}
    log_event(
        "Mailman returned X items",
        count=item_count,
        mode=digest.get("mode", mode) if isinstance(digest, dict) else mode,
        mailman_digest_count=int(summary.get("total", item_count) or 0),
    )
    return digest


def build_mailman_highlights_digest() -> str:
    return render_mailman_digest(call_mailman_digest(unread_only=True, mode=MAILMAN_RECENT_MODE))


def unread_email_summary_text(item: dict) -> str:
    return display_field(item.get("highlight") or item.get("why_it_matters") or item.get("subject"), 110)


def unread_email_action_text(item: dict) -> str:
    action = display_field(item.get("action"), 80)
    return "Review" if action == "—" else action


def unread_email_title_text(item: dict) -> str:
    subject = display_field(item.get("subject"), 70)
    when = " ".join(part for part in (compact_text(item.get("date"), 28), compact_text(item.get("time"), 18)) if part)
    return f"{subject} – {when}" if when else subject


def unread_email_message_id(item: dict) -> str:
    return str(item.get("gmail_message_id") or item.get("message_id") or "")


def unread_email_initial_status(item: dict) -> str:
    return "unread" if item.get("unread", True) else "read"


def register_unread_feed(items: list[dict], chat_id: int | None) -> int:
    feed_id = next_unread_feed_id()
    feed_items = []
    batches = []
    for index, item in enumerate(items, start=1):
        batch_index = (index - 1) // UNREAD_EMAIL_CHUNK_SIZE
        feed_items.append(
            {
                "number": index,
                "batch": batch_index,
                "item": item,
                "message_id": unread_email_message_id(item),
                "subject": item.get("subject"),
                "status": unread_email_initial_status(item),
            }
        )
    for batch_index, start in enumerate(range(0, len(feed_items), UNREAD_EMAIL_CHUNK_SIZE)):
        indexes = list(range(start + 1, min(start + UNREAD_EMAIL_CHUNK_SIZE, len(feed_items)) + 1))
        batches.append({"index": batch_index, "indexes": indexes, "message_id": None})
    UNREAD_FEEDS[feed_id] = {
        "id": feed_id,
        "chat_id": chat_id,
        "items": feed_items,
        "batches": batches,
        "global_message_id": None,
        "created_at": utc_iso_now(),
    }
    return feed_id


def unread_feed_item(feed: dict, item_number: int) -> dict | None:
    items = feed.get("items", [])
    if 1 <= item_number <= len(items):
        return items[item_number - 1]
    return None


def unread_feed_batch(feed: dict, batch_index: int) -> dict | None:
    batches = feed.get("batches", [])
    if 0 <= batch_index < len(batches):
        return batches[batch_index]
    return None


def unread_batch_keyboard(feed_id: int, batch_index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Mark These Read", callback_data=f"unread:br:{feed_id}:{batch_index}"),
                InlineKeyboardButton("🧠 Summarize These", callback_data=f"unread:bs:{feed_id}:{batch_index}"),
            ]
        ]
    )


def unread_global_keyboard(feed_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Mark All Read", callback_data=f"unread:ar:{feed_id}:all"),
                InlineKeyboardButton("📬 Leave Unread", callback_data=f"unread:leave:{feed_id}:all"),
            ]
        ]
    )


def gmail_modify_error_message(detail: str | None = None) -> str:
    text = (detail or "").strip()
    lowered = text.lower()
    if "modify scope" in lowered or "insufficient" in lowered or "scope" in lowered:
        return "Gmail modify access is missing. Re-authorize with: python3 bubbles.py --google-auth"
    if text:
        return compact_text(text, 300)
    return "I couldn’t update that email right now."


def mailman_item_message_id(item: dict) -> str:
    return str(item.get("gmail_message_id") or item.get("message_id") or "")


def mailman_item_was_shown_today(item: dict) -> bool:
    message_id = mailman_item_message_id(item)
    if not message_id:
        return False
    return any(value.endswith(f":{message_id}") for value in shown_scan_item_ids_today())


def update_unread_flow_debug(
    *,
    mode: str,
    query: str,
    raw_count: int | None,
    raw_error: str | None,
    digest: dict | None,
    items: list[dict],
    final_count: int,
) -> None:
    surfaced_ids = gmail_previously_surfaced_ids()
    hidden_by_memory = sum(1 for item in items if mailman_item_message_id(item) in surfaced_ids)
    shown_today = sum(1 for item in items if mailman_item_was_shown_today(item))
    mailman_count = len(items)
    summary = digest.get("summary", {}) if isinstance(digest, dict) and isinstance(digest.get("summary"), dict) else {}
    errors = digest.get("errors", []) if isinstance(digest, dict) and isinstance(digest.get("errors"), list) else []
    LAST_UNREAD_FLOW_DEBUG.clear()
    LAST_UNREAD_FLOW_DEBUG.update(
        {
            "at": utc_iso_now(),
            "mode": mode,
            "query": query,
            "raw_unread_count": raw_count,
            "raw_error": raw_error or "",
            "mailman_digest_count": int(summary.get("total", mailman_count) or 0),
            "mailman_items_loaded": mailman_count,
            "hidden_by_memory_count": hidden_by_memory,
            "after_memory_filter_count": mailman_count - hidden_by_memory,
            "shown_today_count": shown_today,
            "after_shown_today_filter_count": mailman_count - shown_today,
            "final_count_sent": final_count,
            "mailman_errors": len(errors),
        }
    )
    log_event(
        "unread_flow_debug",
        mode=mode,
        query=query,
        raw_unread_count=raw_count if raw_count is not None else "unknown",
        mailman_digest_count=LAST_UNREAD_FLOW_DEBUG["mailman_digest_count"],
        hidden_by_memory=hidden_by_memory,
        shown_today=shown_today,
        final_count=final_count,
        raw_error=raw_error or "",
        mailman_errors=len(errors),
    )


def render_unread_email_feed_message(items: list[dict], start: int, end: int) -> str:
    lines = ["", "📬 Unread Emails", ""]
    for display_index, item in enumerate(items[start:end], start=start + 1):
        emoji = item.get("emoji") or "📩"
        sender = display_field(item.get("from_display") or item.get("from"), 76)
        lines.extend(
            [
                f"{display_index}. {emoji} {unread_email_title_text(item)}",
                f"From: {sender}",
                f"Summary: {display_field(unread_email_summary_text(item), 100)}",
                f"Action: {display_field(unread_email_action_text(item), 72)}",
                "",
            ]
        )
    return "\n".join(lines)[:4000]


def mailman_error_text(errors: list) -> str:
    first_error = errors[0] if errors else {}
    message = first_error.get("message") if isinstance(first_error, dict) else str(first_error)
    return f"I couldn’t load unread emails right now.\n\n{compact_text(str(message), 220)}"


async def send_unread_email_summary(message) -> bool:
    mode = MAILMAN_FULL_UNREAD_MODE
    query = FULL_UNREAD_QUERY
    raw_count, raw_error = gmail_query_count(query)
    try:
        digest = call_mailman_digest(unread_only=True, mode=mode)
    except Exception as e:
        update_unread_flow_debug(
            mode=mode,
            query=query,
            raw_count=raw_count,
            raw_error=raw_error,
            digest=None,
            items=[],
            final_count=0,
        )
        await message.reply_text(
            f"I couldn’t load unread emails right now.\n\n{compact_text(str(e), 220)}",
            disable_web_page_preview=True,
        )
        return False

    items = digest.get("items", []) if isinstance(digest.get("items"), list) else []
    errors = digest.get("errors", []) if isinstance(digest.get("errors"), list) else []
    update_unread_flow_debug(
        mode=digest.get("mode", mode) if isinstance(digest, dict) else mode,
        query=query,
        raw_count=raw_count,
        raw_error=raw_error,
        digest=digest,
        items=items,
        final_count=len(items),
    )
    if errors and not items:
        await message.reply_text(mailman_error_text(errors), disable_web_page_preview=True)
        return False
    if not items:
        await message.reply_text("You’re caught up — I don’t see unread emails right now.", disable_web_page_preview=True)
        return False

    CONTROLLER_STATE["latest_email_choices"] = [
        {
            "number": index,
            "position": ((index - 1) % UNREAD_EMAIL_CHUNK_SIZE) + 1,
            "item": item,
            "message_id": item.get("gmail_message_id") or item.get("message_id"),
            "subject": item.get("subject"),
        }
        for index, item in enumerate(items, start=1)
    ]
    controller_remember_email_item(items[-1], None)
    chat_id = getattr(message, "chat_id", None) or getattr(getattr(message, "chat", None), "id", None)
    feed_id = register_unread_feed(items, chat_id)
    feed = UNREAD_FEEDS[feed_id]
    await message.reply_text(f"📬 You have {len(items)} unread email{'s' if len(items) != 1 else ''}. Here’s everything:", disable_web_page_preview=True)
    for batch_index, start in enumerate(range(0, len(items), UNREAD_EMAIL_CHUNK_SIZE)):
        end = min(start + UNREAD_EMAIL_CHUNK_SIZE, len(items))
        sent = await message.reply_text(
            render_unread_email_feed_message(items, start, end),
            reply_markup=unread_batch_keyboard(feed_id, batch_index),
            disable_web_page_preview=True,
        )
        if batch_index < len(feed.get("batches", [])):
            feed["batches"][batch_index]["message_id"] = getattr(sent, "message_id", None)
    global_message = await message.reply_text(
        "📬 Actions for all shown",
        reply_markup=unread_global_keyboard(feed_id),
        disable_web_page_preview=True,
    )
    feed["global_message_id"] = getattr(global_message, "message_id", None)
    return True


def operator_group_name(item: dict) -> str:
    item_type = str(item.get("type", ""))
    if item_type == "appointment":
        return "appointments"
    if item_type in {"bill", "payment"}:
        return "bills"
    if item_type == "security":
        return "security"
    return "others"


def operator_group_priority(group: str) -> int:
    order = {
        "appointments": 0,
        "bills": 1,
        "security": 2,
        "others": 3,
    }
    return order.get(group, 99)


def build_operator_summary(limit: int = 3) -> dict:
    if mailman_build_digest is None:
        detail = f": {MAILMAN_IMPORT_ERROR}" if MAILMAN_IMPORT_ERROR else ""
        raise RuntimeError(f"Mailman is not available{detail}")

    digest = call_mailman_digest(unread_only=True, limit=12, mode=MAILMAN_RECENT_MODE)
    items = digest.get("items", []) if isinstance(digest, dict) else []
    grouped = {
        "appointments": [],
        "bills": [],
        "security": [],
        "others": [],
    }

    for item in items:
        if not isinstance(item, dict):
            continue
        grouped[operator_group_name(item)].append(item)

    ordered: list[dict] = []
    for group in sorted(grouped, key=operator_group_priority):
        ordered.extend(grouped[group])

    return {
        "digest": digest,
        "grouped": grouped,
        "top_items": ordered[: max(1, limit)],
    }


def operator_item_brief(item: dict) -> str:
    title = display_field(item.get("subject"), 72)
    date_text = display_field(item.get("date"), 32)
    time_text = display_field(item.get("time"), 24)
    if operator_group_name(item) == "appointments" and date_text and time_text:
        return f"{title} {date_text} at {time_text}"
    if operator_group_name(item) == "appointments" and date_text:
        return f"{title} {date_text}"
    return display_field(item.get("why_it_matters") or item.get("highlight") or title, 96)


def operator_item_line(item: dict) -> str:
    emoji = item.get("emoji") or "📩"
    return f"• {emoji} {operator_item_brief(item)}"


def build_operator_response() -> str:
    summary = build_operator_summary(limit=3)
    digest = summary.get("digest", {}) if isinstance(summary, dict) else {}
    errors = digest.get("errors", []) if isinstance(digest, dict) else []
    top_items = summary.get("top_items", []) if isinstance(summary, dict) else []

    if not top_items:
        if errors:
            first_error = errors[0] if isinstance(errors[0], dict) else {}
            message = first_error.get("message") if isinstance(first_error, dict) else str(errors[0])
            return f"I couldn’t pull things together cleanly.\n\n• {compact_text(str(message), 220)}"
        return "I checked things over.\n\n• Nothing unread is waiting right now."

    lines = ["Here’s what stands out:", ""]
    for item in top_items[:3]:
        lines.append(operator_item_line(item))
    lines.extend(["", "If you want, I can help you handle one of these."])
    return "\n".join(lines)[:4000]


def render_operator_summary_text(summary: dict) -> str:
    return build_operator_response()


def render_operator_actions_text(summary: dict) -> str:
    top_items = summary.get("top_items", []) if isinstance(summary, dict) else []
    if not top_items:
        return "Right now I don’t see any urgent actions for you."

    lines = ["Here’s what I’d do next:", ""]
    for index, item in enumerate(top_items[:3], start=1):
        action = display_field(item.get("action"), 72) or "Review it"
        lines.append(f"{index}. {action}")
    lines.extend(["", "Want me to help with one?"])
    return "\n".join(lines)[:4000]


def event_occurs_on_date(event: dict, target_date) -> bool:
    start = event.get("start", {}) if isinstance(event.get("start"), dict) else {}
    start_dt = parse_event_datetime(start.get("dateTime", ""))
    if start_dt:
        return start_dt.date() == target_date
    start_date = start.get("date")
    if not start_date:
        return False
    try:
        return datetime.fromisoformat(start_date).date() == target_date
    except ValueError:
        return False


def today_calendar_events(max_results: int = 25) -> list[dict]:
    today = datetime.now(LOCAL_TZ).date()
    events = list_calendar_events(days=2, max_results=max_results)
    return [event for event in events if event_occurs_on_date(event, today)]


def build_daily_briefing_text() -> str:
    summary = build_operator_summary(limit=3)
    today_events = today_calendar_events()
    top_items = summary.get("top_items", []) if isinstance(summary, dict) else []

    lines = ["Here’s your day:", ""]

    if today_events:
        lines.append("Today:")
        for event in today_events[:3]:
            title = display_field(event.get("summary"), 60)
            start = event.get("start", {}) if isinstance(event.get("start"), dict) else {}
            start_dt = parse_event_datetime(start.get("dateTime", ""))
            if start_dt:
                lines.append(f"• 📅 {format_local_clock(start_dt.strftime('%H:%M'))} {title}")
            else:
                lines.append(f"• 📅 {title}")
    else:
        lines.append("Today:")
        lines.append("• 📅 Nothing on your calendar yet")

    if top_items:
        lines.extend(["", "What matters:"])
        for item in top_items[:2]:
            lines.append(operator_item_line(item))
        suggestion = display_field(top_items[0].get("action"), 72) or "Check your inbox"
        lines.extend(["", f"Suggestion: {suggestion}"])
    else:
        lines.extend(["", "What matters:", "• No email items to review right now", "", "Suggestion: Use the open time to clear one small task."])

    return "\n".join(lines)[:4000]


def build_legacy_highlights_digest() -> str:
    result = scan_recent_email_for_updates(unread_only=True, persistent_new_only=False)
    items = build_scan_items(result, include_shown=True)
    for item in items[:10]:
        message_id = scan_item_message_id(item)
        if message_id:
            remember_gmail_id("shown", message_id)
    log_event("highlights_built", items=len(items), important=len(result.get("important", [])))
    return format_highlights_digest(items[:8], result)


def build_highlights_digest() -> str:
    try:
        text = build_mailman_highlights_digest()
        log_event("mailman_highlights_built")
        return text
    except Exception as e:
        log_event("mailman_highlights_failed", error=e.__class__.__name__)
        try:
            legacy = build_legacy_highlights_digest()
            return f"Mailman highlights are unavailable right now. Showing legacy highlights.\n\n{legacy}"[:4000]
        except Exception:
            return f"Mailman highlights are unavailable right now: {compact_text(str(e), 220)}"


def build_scan_items(result: dict, include_shown: bool = False) -> list[dict]:
    items = []
    candidate_by_source = result.get("candidate_by_source", {})
    for email_item in result.get("important", []):
        candidate = candidate_by_source.get(email_item.get("id"))
        if candidate:
            index = next(
                (
                    item_index
                    for item_index, item in enumerate(PENDING_EMAIL_APPOINTMENTS, start=1)
                    if item.get("source_id") == email_item.get("id")
                ),
                None,
            )
            if index:
                items.append({"type": "appointment", "index": index})
            continue
        items.append({"type": "email", "email": email_item, "kind": "important"})
    for email_item in result.get("promo_picks", []):
        items.append({"type": "promo", "email": email_item, "kind": "promo"})
    items = rank_scan_items(items)
    if include_shown:
        return items
    return [item for item in items if not scan_item_was_shown_today(item)]


def has_unshown_scan_items(items: list[dict], start: int = 0) -> bool:
    return any(not scan_item_was_shown_today(item) for item in items[start:])


def build_scan_digest(unread_only: bool = False, persistent_new_only: bool = False) -> dict:
    result = scan_recent_email_for_updates(unread_only=unread_only, persistent_new_only=persistent_new_only)
    all_items = build_scan_items(result, include_shown=True)
    items = [item for item in all_items if not scan_item_was_shown_today(item)]
    return {"result": result, "all_items": all_items, "items": items}


def build_ranked_digest() -> dict:
    return build_scan_digest()


def build_proactive_digest() -> dict:
    return build_scan_digest(unread_only=True, persistent_new_only=True)


async def reply_scan_text(message, text: str, reply_markup: InlineKeyboardMarkup | None = None):
    await message.reply_text(text, reply_markup=reply_markup, disable_web_page_preview=True)


class BotMessageTarget:
    def __init__(self, bot, chat_id: int):
        self.bot = bot
        self.chat_id = chat_id

    async def reply_text(self, text: str, reply_markup: InlineKeyboardMarkup | None = None, disable_web_page_preview: bool = True):
        await self.bot.send_message(
            chat_id=self.chat_id,
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )


async def send_scan_page(message, batch_id: int, start: int = 0) -> bool:
    batch = SCAN_BATCHES.get(batch_id)
    if not batch:
        return False
    items = batch.get("items", [])
    total = len(items)
    page_items = []
    cursor = start
    while cursor < total and len(page_items) < SCAN_PAGE_SIZE:
        item = items[cursor]
        cursor += 1
        if scan_item_was_shown_today(item):
            continue
        page_items.append(item)
    if not page_items:
        batch["offset"] = cursor
        return False
    shown_start = start + 1
    shown_end = cursor
    await reply_scan_text(message, scan_header_text(total, shown_start, shown_end))
    for item in page_items:
        if item.get("type") == "appointment":
            index = item.get("index")
            if isinstance(index, int) and 1 <= index <= len(PENDING_EMAIL_APPOINTMENTS):
                await reply_scan_text(
                    message,
                    format_appointment_candidate(PENDING_EMAIL_APPOINTMENTS[index - 1], index)[:4000],
                    reply_markup=appointment_keyboard(index),
                )
                mark_scan_item_shown_today(item)
                remember_actionable_item("appointment", index, PENDING_EMAIL_APPOINTMENTS[index - 1].get("title", ""))
                log_event("card_shown", type="appointment", message_id=scan_item_message_id(item))
            continue
        email_item = item.get("email", {})
        card_id = register_email_card(email_item, item.get("kind", "important"))
        formatter = format_promo_card if item.get("type") == "promo" else format_important_email_card
        await reply_scan_text(message, formatter(email_item)[:4000], reply_markup=email_card_keyboard(card_id))
        mark_scan_item_shown_today(item)
        remember_actionable_item("email", card_id, email_item.get("subject", ""))
        log_event("card_shown", type=item.get("type", "email"), message_id=email_item.get("id", ""))
    batch["offset"] = cursor
    more_available = bool(batch.get("allow_more", True) and has_unshown_scan_items(items, cursor))
    controller_set_more("scan", batch_id, more_available)
    if more_available:
        await reply_scan_text(message, "Show more scanned emails?", reply_markup=scan_more_keyboard(batch_id))
    return True


async def send_scan_batch(message, digest: dict | None = None, allow_more: bool = True, limit: int | None = None) -> bool:
    digest = digest or build_scan_digest()
    result = digest.get("result", {})
    all_items = digest.get("all_items", [])
    items = digest.get("items", [])
    if limit is not None:
        items = items[:limit]
    if not all_items:
        await reply_scan_text(message, render_scan_result(result)[:4000])
        return False
    if not items:
        await reply_scan_text(
            message,
            "You've already reviewed today's scanned items here. Scroll up to revisit them, or scan again tomorrow for a fresh set.",
        )
        return False
    batch_id = next_scan_batch_id()
    SCAN_BATCHES[batch_id] = {"items": items, "offset": 0, "allow_more": allow_more}
    CONTROLLER_STATE["active_scan_batch_id"] = batch_id
    sent = await send_scan_page(message, batch_id, 0)
    if not sent:
        await reply_scan_text(
            message,
            "You've already reviewed today's scanned items here. Scroll up to revisit them, or scan again tomorrow for a fresh set.",
        )
    return sent


def today_email_summary_text() -> str:
    if not TODAY_IMPORTANT_EMAILS:
        result = scan_recent_email_for_updates()
        if result.get("errors") and not result.get("important"):
            return "\n".join(result["errors"][:3])
    if not TODAY_IMPORTANT_EMAILS:
        return "No important emails found for today yet."
    lines = ["Today's important email summary:"]
    for email_item in TODAY_IMPORTANT_EMAILS[:8]:
        lines.append(f"- {email_highlight(email_item)}")
    return "\n".join(lines)


def add_pending_candidate(index: int) -> str:
    if index < 1 or index > len(PENDING_EMAIL_APPOINTMENTS):
        return "That pending item number is not available."
    candidate = PENDING_EMAIL_APPOINTMENTS[index - 1]
    if candidate.get("status", "pending") != "pending":
        return handled_candidate_text("add" if candidate.get("status") == "added" else "skip", index)
    validation_error = validate_email_candidate(candidate)
    if validation_error:
        return f"I can’t add this yet.\n\n{validation_error}\n\nAsk me to show the pending items and I’ll walk you through it."
    event = create_calendar_event(
        candidate["title"],
        candidate_date_text(candidate),
        True,
        "2",
        candidate.get("description", ""),
        60,
        candidate.get("location", ""),
        [60, 1440],
    )
    candidate["status"] = "added"
    remember_gmail_id("calendar_added", candidate.get("source_id", ""))
    log_event("calendar_event_added", source_id=candidate.get("source_id", ""), title=candidate.get("title", ""))
    return (
        "✅ Added to your calendar\n\n"
        f"Title: {event.get('summary', candidate['title'])}\n"
        f"When: {event_start_text(event)}\n"
        f"Location: {candidate.get('location') or 'Not set'}"
    )


def send_morning_summary(send_func=None) -> str:
    text = "Good morning. " + today_email_summary_text()
    if send_func:
        send_func(text)
    return text


def send_afternoon_summary(send_func=None) -> str:
    text = "Afternoon check-in. " + today_email_summary_text()
    if send_func:
        send_func(text)
    return text


def send_evening_summary(send_func=None) -> str:
    text = "Evening wrap-up. " + today_email_summary_text()
    if send_func:
        send_func(text)
    return text


async def run_scheduled_email_scan(message, label: str = "Scheduled email scan", proactive: bool = False) -> bool:
    log_event("scheduled_scan_started", label=label, proactive=proactive)
    digest = build_proactive_digest() if proactive else build_ranked_digest()
    items = digest.get("items", [])[:3]
    if not items:
        log_event("scheduled_scan_finished", label=label, shown=0)
        return False
    await reply_scan_text(message, scheduled_digest_intro(label.split()[0], items))
    sent = await send_scan_batch(message, digest, allow_more=False, limit=3)
    record_digest_history(label, len(items) if sent else 0)
    log_event("scheduled_scan_finished", label=label, shown=len(items) if sent else 0)
    return sent


async def run_scheduled_scan(message, label: str = "Scheduled digest") -> bool:
    return await run_scheduled_email_scan(message, label)


async def run_morning_digest(message) -> bool:
    return await run_scheduled_email_scan(message, "Morning email digest")


async def run_afternoon_digest(message) -> bool:
    return await run_scheduled_email_scan(message, "Afternoon email digest")


async def run_evening_digest(message) -> bool:
    return await run_scheduled_email_scan(message, "Evening email digest")


async def run_proactive_digest_message(message, label: str) -> bool:
    return await run_scheduled_email_scan(message, label, proactive=True)


async def send_morning_email_digest(message) -> bool:
    return await run_morning_digest(message)


async def send_afternoon_email_digest(message) -> bool:
    return await run_afternoon_digest(message)


async def send_evening_email_digest(message) -> bool:
    return await run_evening_digest(message)


def parse_digest_time(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value or "")
    if not match:
        return 8, 0
    hour = max(0, min(int(match.group(1)), 23))
    minute = max(0, min(int(match.group(2)), 59))
    return hour, minute


def digest_schedule() -> dict[str, str]:
    return {
        "morning": MORNING_DIGEST_TIME,
        "afternoon": AFTERNOON_DIGEST_TIME,
        "evening": EVENING_DIGEST_TIME,
    }


def schedule_status_text() -> str:
    enabled = "yes" if PROACTIVE_DIGESTS_ENABLED else "no"
    return (
        "Proactive digests\n\n"
        f"Enabled: {enabled}\n"
        f"Morning: {MORNING_DIGEST_TIME}\n"
        f"Afternoon: {AFTERNOON_DIGEST_TIME}\n"
        f"Evening: {EVENING_DIGEST_TIME}\n"
        f"Timezone: {GOOGLE_CALENDAR_TIMEZONE}"
    )


def digest_run_key(label: str, date_key: str) -> str:
    return f"{date_key}:{label}"


def digest_already_ran(label: str, date_key: str) -> bool:
    key = digest_run_key(label, date_key)
    return any(item.get("key") == key for item in load_memory().get("digest_history", []) if isinstance(item, dict))


def mark_digest_ran(label: str, date_key: str, shown_count: int) -> None:
    memory = load_memory()
    history = memory.setdefault("digest_history", [])
    history.append({"key": digest_run_key(label, date_key), "at": utc_iso_now(), "label": label, "shown": shown_count})
    memory["digest_history"] = history[-60:]
    save_memory(memory)


async def run_proactive_digest(application, label: str) -> bool:
    if ALLOWED_USER_ID is None:
        return False
    target = BotMessageTarget(application.bot, ALLOWED_USER_ID)
    sent = await run_scheduled_email_scan(target, f"{label.title()} email digest", proactive=True)
    if label == "evening" and not sent:
        log_event("scheduled_scan_empty", label=label)
    return sent


async def proactive_digest_loop(application) -> None:
    log_event("scheduler_started", enabled=PROACTIVE_DIGESTS_ENABLED)
    while PROACTIVE_DIGESTS_ENABLED:
        now = datetime.now(LOCAL_TZ)
        date_key = now.date().isoformat()
        for label, time_value in digest_schedule().items():
            hour, minute = parse_digest_time(time_value)
            scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            window_end = scheduled + timedelta(minutes=60)
            if scheduled <= now < window_end and not digest_already_ran(label, date_key):
                try:
                    sent = await run_proactive_digest(application, label)
                    mark_digest_ran(label, date_key, 1 if sent else 0)
                except Exception as e:
                    log_event("scheduled_scan_error", label=label, error=e.__class__.__name__)
        await asyncio.sleep(DIGEST_CHECK_INTERVAL_SECONDS)


async def start_background_tasks(application) -> None:
    log_event("startup", proactive=PROACTIVE_DIGESTS_ENABLED)
    if PROACTIVE_DIGESTS_ENABLED:
        application.create_task(proactive_digest_loop(application))


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


def ollama_mode_label() -> str:
    if OLLAMA_MODE == "chat":
        return "chat (generate fallback if /api/chat returns 404)"
    return "generate fallback"


def enabled_features() -> list[str]:
    features = [
        "Google Calendar actions",
        "natural calendar parsing",
        "Ollama chat",
        "system status",
        "file read/write commands",
    ]
    if BUBBLES_ENABLE_DEV_COMMANDS:
        features.append("dev test event seeding")
    return features


def safe_ollama_text() -> str:
    global LAST_OLLAMA_HEALTH
    health = ollama_diagnostics(OLLAMA_HEALTH_TIMEOUT_SECONDS)
    LAST_OLLAMA_HEALTH = health
    tags = health["tags"]
    model_installed = "yes" if health["model_installed"] else "no"
    remaining = ollama_cooldown_remaining()
    if remaining:
        state = "cooldown"
    elif tags.get("ok"):
        state = "online"
    else:
        state = "offline"
    last_error = OLLAMA_LAST_ERROR or "none"
    cooldown_suffix = f" ({remaining}s remaining)" if remaining else ""

    return (
        "Ollama brain\n\n"
        f"Base URL: {OLLAMA_BASE_URL}\n"
        f"Configured model: {MODEL}\n"
        f"/api/tags: {format_ollama_check_result(tags)}\n"
        f"Model installed: {model_installed}\n"
        f"Chat brain state: {state}{cooldown_suffix}\n"
        f"Last error type: {last_error}\n"
        f"Consecutive failures: {OLLAMA_CONSECUTIVE_FAILURES}\n"
        f"Consecutive timeouts: {OLLAMA_CONSECUTIVE_TIMEOUTS}\n"
        f"Timeouts: connect {OLLAMA_CONNECT_TIMEOUT_SECONDS}s, read {OLLAMA_READ_TIMEOUT_SECONDS}s\n"
        f"Cooldown: {OLLAMA_COOLDOWN_SECONDS}s after {OLLAMA_FAILURE_COOLDOWN_THRESHOLD} failure(s)\n"
        f"Prompt caps: {OLLAMA_MAX_HISTORY_TURNS} turns, {OLLAMA_PROMPT_CHAR_LIMIT} chars"
    )


def safe_about_text() -> str:
    global LAST_OLLAMA_HEALTH
    health = ollama_diagnostics(OLLAMA_HEALTH_TIMEOUT_SECONDS)
    LAST_OLLAMA_HEALTH = health
    tags = health["tags"]
    health_label = format_ollama_check_result(tags)
    model_installed = "yes" if health["model_installed"] else "no"
    platform_label = f"{platform.system()} {platform.machine()}".strip()
    python_label = platform.python_version()
    dev_status = "enabled" if BUBBLES_ENABLE_DEV_COMMANDS else "disabled"

    return (
        "Bubbles self-info\n\n"
        f"Running on: {platform_label}; Python {python_label}\n"
        f"Ollama base URL: {OLLAMA_BASE_URL}\n"
        f"Configured model: {MODEL}\n"
        f"Ollama mode: {ollama_mode_label()}\n"
        f"Ollama health: {health_label}; model installed: {model_installed}\n"
        f"Timezone: {GOOGLE_CALENDAR_TIMEZONE}\n"
        f"Enabled features: {', '.join(enabled_features())}; dev commands {dev_status}\n\n"
        "Commands: /start, /about, /helpme, /ollama, /brain, /id, /status, /calendar [days], /next, "
        "/free [days], /scan, /highlights, /assistanttest, /scheduledtest, /rundigest, /schedule, "
        "/memory, /logtail, /gmailstatus, /summary, /pending, /add, /skip, /addall, /clearpending, /resetbrain, "
        "/add_event, /calendar_setup, /ls, /read, /write.\n\n"
        "Natural requests: create or schedule calendar events, show upcoming appointments, "
        "ask for the next appointment, ask for the next available day, and save usual "
        "appointment locations or reminders."
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    await update.message.reply_text(
        "🤖 Bubbles is online.\n\n"
        "Commands:\n"
        "/about\n"
        "/helpme\n"
        "/ollama\n"
        "/brain\n"
        "/id\n"
        "/status\n"
        "/calendar [days]\n"
        "/next\n"
        "/free [days]\n"
        "/scan\n"
        "/highlights\n"
        "/assistanttest\n"
        "/scheduledtest\n"
        "/rundigest\n"
        "/schedule\n"
        "/memory\n"
        "/logtail\n"
        "/gmailstatus\n"
        "/summary\n"
        "/pending\n"
        "/add <number>\n"
        "/skip <number>\n"
        "/addall\n"
        "/clearpending\n"
        "/resetbrain\n"
        "/add_event <title> | <date> | <reminders yes/no> | <reminder count> | [description]\n"
        "/calendar_setup\n"
        "/ls [path]\n"
        "/read <path>\n"
        "/write <path> | <content>\n\n"
        "You can also ask things like \"What's my next upcoming appointment?\" "
        "or \"Create a call with Sam for the 25th of April.\""
    )


async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    await update.message.reply_text(safe_about_text()[:4000])


async def ollama_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    await update.message.reply_text(safe_ollama_text()[:4000])


async def resetbrain_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    reset_ollama_state()
    await update.message.reply_text("Chat brain cooldown cleared.")


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

    service_status = service_status_text()
    cpu = run_command(["bash", "-lc", "uptime"])
    ram = run_command(["bash", "-lc", "free -h"])
    disk = run_command(["bash", "-lc", "df -h /"])

    reply = f"{service_status}\n\n🖥️ System Status\n\nUptime:\n{cpu}\n\nRAM:\n{ram}\n\nDisk:\n{disk}"
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


async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    log_event("manual_scan_started")
    await send_scan_results(update)
    log_event("manual_scan_finished")


async def scheduledtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    sent = await run_scheduled_email_scan(update.message, "Scheduled email scan test")
    if not sent:
        await update.message.reply_text("No new scheduled email items to show right now.", disable_web_page_preview=True)


async def assistanttest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    sent = await run_morning_digest(update.message)
    if not sent:
        await update.message.reply_text("No new assistant digest items to show right now.", disable_web_page_preview=True)


async def rundigest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    sent = await run_proactive_digest_message(update.message, "Manual email digest")
    if not sent:
        await update.message.reply_text("No new digest items right now.", disable_web_page_preview=True)


async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    await update.message.reply_text(schedule_status_text()[:4000], disable_web_page_preview=True)


async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    await update.message.reply_text(memory_summary_text()[:4000], disable_web_page_preview=True)


async def logtail_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    lines = read_log_tail(10)
    await update.message.reply_text("\n".join(lines)[:4000] if lines else "No log lines found.", disable_web_page_preview=True)


def workflow_text() -> str:
    return (
        "🧭 Bubbles ↔ Mailman Workflow\n\n"
        "1. You send a Telegram command to Bubbles\n"
        "2. Bubbles decides whether email intelligence is needed\n"
        "3. Bubbles calls Mailman\n"
        "4. Mailman scans Gmail and builds a ranked digest\n"
        "5. Mailman returns structured data to Bubbles\n"
        "6. Bubbles formats the digest into Telegram messages\n"
        "7. You approve actions with buttons\n\n"
        "Current setup:\n"
        "Bubbles: Telegram UI + actions\n"
        "Mailman: Email scanning + ranking + summaries"
    )


async def workflow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    await update.message.reply_text(workflow_text()[:4000], disable_web_page_preview=True)


def render_mailman_test_result(digest: dict) -> str:
    log_event("Bubbles rendering Mailman digest")
    summary = digest.get("summary", {}) if isinstance(digest.get("summary"), dict) else {}
    items = digest.get("items", []) if isinstance(digest.get("items"), list) else []
    errors = digest.get("errors", []) if isinstance(digest.get("errors"), list) else []
    lines = [
        "🧪 Mailman Test",
        "",
        "Status: succeeded" if not errors else "Status: succeeded with account issues",
        "",
        "Summary:",
        f"📩 {int(summary.get('total', 0) or 0)} unread",
        mailman_summary_line(summary, "appointments", "appointment", "📅"),
        mailman_summary_line(summary, "bills", "bill", "💸", "bills"),
        mailman_summary_line(summary, "security", "security", "🔐", "security"),
        mailman_summary_line(summary, "promos", "promo", "🎁"),
    ]
    if items:
        lines.extend(["", "First items:"])
        for index, item in enumerate(items[:2], start=1):
            if not isinstance(item, dict):
                continue
            title = display_field(item.get("subject"), 90)
            item_type = display_field(item.get("type"), 40)
            priority = display_field(item.get("priority"), 40)
            emoji = item.get("emoji") or "📩"
            lines.append(f"{index}. {emoji} {title}")
            lines.append(f"   Type: {item_type} · Priority: {priority}")
    else:
        lines.extend(["", "First items: none"])
    if errors:
        lines.extend(["", f"Account issues: {len(errors)}"])
    return "\n".join(lines)[:4000]


async def mailmantest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    try:
        digest = call_mailman_digest(unread_only=True, mode=MAILMAN_RECENT_MODE)
        await update.message.reply_text(render_mailman_test_result(digest), disable_web_page_preview=True)
    except Exception as e:
        log_event("mailman_test_failed", error=e.__class__.__name__)
        await update.message.reply_text(
            f"Mailman test failed: {compact_text(str(e), 220)}",
            disable_web_page_preview=True,
        )


async def highlights_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    await update.message.reply_text(build_highlights_digest(), disable_web_page_preview=True)


async def send_scan_results(update: Update) -> None:
    await send_scan_batch(update.message)


async def email_summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    await update.message.reply_text(today_email_summary_text()[:4000])


async def pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    await update.message.reply_text(pending_email_summary()[:4000])


async def gmail_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    await update.message.reply_text(gmail_status_text()[:4000])


def debug_mail_text() -> str:
    mode = MAILMAN_FULL_UNREAD_MODE
    query = FULL_UNREAD_QUERY
    raw_count, raw_error = gmail_query_count(query)
    digest = None
    items: list[dict] = []
    mailman_error = ""
    try:
        digest = call_mailman_digest(unread_only=True, mode=mode)
        items = digest.get("items", []) if isinstance(digest.get("items"), list) else []
    except Exception as e:
        mailman_error = compact_text(str(e), 220)
    update_unread_flow_debug(
        mode=digest.get("mode", mode) if isinstance(digest, dict) else mode,
        query=query,
        raw_count=raw_count,
        raw_error=raw_error,
        digest=digest,
        items=items,
        final_count=len(items),
    )
    memory = load_memory()
    lines = [
        "Debug mail",
        "",
        f"Mailman mode: {LAST_UNREAD_FLOW_DEBUG.get('mode', mode)}",
        f"Full unread query: {query}",
        f"Recent unread query: {GMAIL_UNREAD_QUERY}",
        f"Raw unread count: {raw_count if raw_count is not None else 'unknown'}",
        f"Mailman digest count: {LAST_UNREAD_FLOW_DEBUG.get('mailman_digest_count', 0)}",
        f"Hidden by memory count: {LAST_UNREAD_FLOW_DEBUG.get('hidden_by_memory_count', 0)}",
        f"Shown today count: {LAST_UNREAD_FLOW_DEBUG.get('shown_today_count', 0)}",
        f"Final count sent to Telegram: {LAST_UNREAD_FLOW_DEBUG.get('final_count_sent', 0)}",
        f"Last scan time: {memory.get('scan', {}).get('last_scan_at') or '—'}",
        f"Last unread check: {LAST_UNREAD_FLOW_DEBUG.get('at') or '—'}",
    ]
    if raw_error:
        lines.extend(["", f"Raw count issue: {compact_text(raw_error, 220)}"])
    if mailman_error:
        lines.extend(["", f"Mailman issue: {mailman_error}"])
    return "\n".join(lines)[:4000]


async def debugmail_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    await update.message.reply_text(debug_mail_text(), disable_web_page_preview=True)


async def day_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    try:
        await update.message.reply_text(build_daily_briefing_text(), disable_web_page_preview=True)
    except Exception as e:
        await update.message.reply_text(
            f"❌ Day briefing error: {compact_text(str(e), 220)}",
            disable_web_page_preview=True,
        )


def handled_candidate_text(action: str, index: int) -> str:
    label = "added to your calendar" if action == "add" else "skipped"
    return f"Appointment #{index} was already {label}."


async def appointment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        return
    if not is_authorized(update):
        await query.answer("Not authorized.", show_alert=True)
        return

    parts = (query.data or "").split(":")
    if len(parts) != 3 or parts[0] != "appt":
        await query.answer()
        return
    action = parts[1]
    try:
        index = int(parts[2])
    except ValueError:
        await query.answer("That appointment number is not valid.", show_alert=True)
        return

    if index < 1 or index > len(PENDING_EMAIL_APPOINTMENTS) or PENDING_EMAIL_APPOINTMENTS[index - 1].get("status", "pending") != "pending":
        await query.answer("That appointment has already been handled.", show_alert=True)
        return

    if action == "add":
        reply = add_pending_candidate(index)
        await query.answer("Added." if reply.startswith("✅") else "Needs more details.", show_alert=not reply.startswith("✅"))
        if query.message and reply.startswith("✅"):
            mark_scan_item_action_today({"type": "appointment", "index": index}, "added_to_calendar")
            await query.edit_message_reply_markup(reply_markup=appointment_status_keyboard(index, "✅ Added to Calendar", "📅 Saved"))
        elif query.message:
            await query.message.reply_text(reply[:4000], disable_web_page_preview=True)
        return

    if action == "skip":
        candidate = PENDING_EMAIL_APPOINTMENTS[index - 1]
        candidate["status"] = "skipped"
        remember_gmail_id("skipped", candidate.get("source_id", ""))
        mark_scan_item_action_today({"type": "appointment", "index": index}, "skipped")
        log_event("appointment_skipped", source_id=candidate.get("source_id", ""), title=candidate.get("title", ""))
        await query.answer()
        if query.message:
            await query.edit_message_reply_markup(reply_markup=appointment_status_keyboard(index, "⏸ Skipped", "📬 Left for later"))
        return

    await query.answer()


async def email_card_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        return
    if not is_authorized(update):
        await query.answer("Not authorized.", show_alert=True)
        return

    parts = (query.data or "").split(":")
    if len(parts) != 3 or parts[0] != "email":
        await query.answer()
        return
    action = parts[1]
    try:
        card_id = int(parts[2])
    except ValueError:
        await query.answer("That email card is not valid.", show_alert=True)
        return

    card = EMAIL_CARDS.get(card_id)
    if not card:
        await query.answer("That email card is no longer available.", show_alert=True)
        return

    if action == "read":
        reply = mark_gmail_message_read(card.get("id", ""))
        if reply.startswith("✅"):
            card["status"] = "read"
            remember_gmail_id("read", card.get("id", ""))
            mark_scan_item_status_today(card.get("scan_identity", ""), "read")
            log_event("email_read", message_id=card.get("id", ""))
            await query.answer()
            if query.message:
                await query.edit_message_reply_markup(reply_markup=email_card_keyboard(card_id))
        else:
            await query.answer("I could not mark that read.", show_alert=True)
            if query.message:
                await query.message.reply_text(reply[:4000], disable_web_page_preview=True)
        return

    if action == "unread":
        reply = mark_gmail_message_unread(card.get("id", ""))
        if reply.startswith("✅"):
            card["status"] = "unread"
            remember_gmail_id("unread", card.get("id", ""))
            log_event("email_unread", message_id=card.get("id", ""))
            await query.answer()
            if query.message:
                await query.edit_message_reply_markup(reply_markup=email_card_keyboard(card_id))
        else:
            await query.answer("I could not mark that unread.", show_alert=True)
            if query.message:
                await query.message.reply_text(reply[:4000], disable_web_page_preview=True)
        return

    if action == "summarize":
        email_item, error = fetch_gmail_message(card.get("id", ""))
        if error:
            email_item = card
        else:
            card.update(email_item)
        remember_gmail_id("summarized", card.get("id", ""))
        mark_scan_item_status_today(card.get("scan_identity", ""), "summarized")
        log_event("email_summarized", message_id=card.get("id", ""))
        await query.answer()
        if query.message:
            await query.message.reply_text(summarize_email_item(email_item)[:4000], disable_web_page_preview=True)
        return

    if action == "skip":
        card["status"] = "skipped"
        remember_gmail_id("skipped", card.get("id", ""))
        mark_scan_item_status_today(card.get("scan_identity", ""), "skipped")
        log_event("email_skipped", message_id=card.get("id", ""))
        await query.answer()
        if query.message:
            await query.edit_message_reply_markup(reply_markup=email_card_keyboard(card_id))
        return

    await query.answer()


def mark_unread_feed_item_state(item_state: dict, desired_status: str) -> tuple[bool, bool, str]:
    current_status = item_state.get("status", "unread")
    if current_status == desired_status:
        return True, False, ""
    message_id = str(item_state.get("message_id") or "")
    if not message_id:
        return False, False, "I don’t have a Gmail message ID for that email."
    reply = mark_gmail_message_unread(message_id) if desired_status == "unread" else mark_gmail_message_read(message_id)
    if not reply.startswith("✅"):
        return False, False, gmail_modify_error_message(reply)
    item_state["status"] = desired_status
    remember_gmail_id("unread" if desired_status == "unread" else "read", message_id)
    log_event(f"unread_feed_marked_{desired_status}", message_id=message_id)
    return True, True, ""


def mark_unread_feed_items(feed: dict, item_numbers: list[int], desired_status: str) -> tuple[int, list[str]]:
    changed = 0
    errors = []
    for item_number in item_numbers:
        item_state = unread_feed_item(feed, int(item_number))
        if not item_state:
            errors.append("One of those emails is no longer available.")
            continue
        ok, did_change, error = mark_unread_feed_item_state(item_state, desired_status)
        if ok and did_change:
            changed += 1
        if error:
            errors.append(error)
    return changed, errors


def unread_batch_summary_text(feed: dict, batch_index: int) -> str:
    batch = unread_feed_batch(feed, batch_index)
    if not batch:
        return "I don’t have that email group anymore."
    lines = ["🧠 Group Summary", ""]
    for item_number in batch.get("indexes", []):
        item_state = unread_feed_item(feed, int(item_number))
        if not item_state:
            continue
        item = item_state.get("item", {})
        title = display_field(item.get("subject"), 72)
        summary = display_field(unread_email_summary_text(item), 110)
        lines.append(f"{int(item_number)}. {title}")
        lines.append(f"   {summary}")
    if len(lines) == 2:
        lines.append("No emails found in this group.")
    return "\n".join(lines)[:4000]


async def refresh_unread_batch_markup(context: ContextTypes.DEFAULT_TYPE, feed: dict, batch_index: int, query=None) -> None:
    batch = unread_feed_batch(feed, batch_index)
    if not batch:
        return
    reply_markup = unread_batch_keyboard(int(feed["id"]), batch_index)
    message_id = batch.get("message_id")
    try:
        if query and query.message and getattr(query.message, "message_id", None) == message_id:
            await query.edit_message_reply_markup(reply_markup=reply_markup)
            return
        chat_id = feed.get("chat_id")
        if chat_id and message_id:
            await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
    except Exception as e:
        log_event("unread_batch_markup_update_failed", error=e.__class__.__name__, batch=batch_index)


async def refresh_unread_global_markup(context: ContextTypes.DEFAULT_TYPE, feed: dict, query=None) -> None:
    message_id = feed.get("global_message_id")
    reply_markup = unread_global_keyboard(int(feed["id"]))
    try:
        if query and query.message and getattr(query.message, "message_id", None) == message_id:
            await query.edit_message_reply_markup(reply_markup=reply_markup)
            return
        chat_id = feed.get("chat_id")
        if chat_id and message_id:
            await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
    except Exception as e:
        log_event("unread_global_markup_update_failed", error=e.__class__.__name__)


async def refresh_unread_feed_markups(context: ContextTypes.DEFAULT_TYPE, feed: dict, query=None) -> None:
    for batch in feed.get("batches", []):
        await refresh_unread_batch_markup(context, feed, int(batch.get("index", 0)), query=query)
    await refresh_unread_global_markup(context, feed, query=query)


async def report_unread_action_errors(query, errors: list[str]) -> None:
    message = gmail_modify_error_message(errors[0] if errors else "")
    await query.answer("I couldn’t update every email.", show_alert=True)
    if query.message:
        await query.message.reply_text(message[:4000], disable_web_page_preview=True)


async def unread_email_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        return
    if not is_authorized(update):
        await query.answer("Not authorized.", show_alert=True)
        return

    parts = (query.data or "").split(":")
    if len(parts) != 4 or parts[0] != "unread":
        await query.answer()
        return
    action = parts[1]
    try:
        feed_id = int(parts[2])
    except ValueError:
        await query.answer("That unread feed is not valid.", show_alert=True)
        return
    feed = UNREAD_FEEDS.get(feed_id)
    if not feed:
        await query.answer("That unread feed is no longer available.", show_alert=True)
        if query.message:
            await query.edit_message_reply_markup(reply_markup=None)
        return

    if action in {"br", "bs"}:
        try:
            batch_index = int(parts[3])
        except ValueError:
            await query.answer("That email batch is not valid.", show_alert=True)
            return
        batch = unread_feed_batch(feed, batch_index)
        if not batch:
            await query.answer("That email batch is no longer available.", show_alert=True)
            return
        if action == "bs":
            await query.answer()
            if query.message:
                await query.message.reply_text(unread_batch_summary_text(feed, batch_index), disable_web_page_preview=True)
            return
        _, errors = mark_unread_feed_items(feed, [int(number) for number in batch.get("indexes", [])], "read")
        await refresh_unread_batch_markup(context, feed, batch_index, query=query)
        await refresh_unread_global_markup(context, feed)
        if errors:
            await report_unread_action_errors(query, errors)
        else:
            await query.answer()
        return

    if action == "ar":
        item_numbers = [int(item.get("number", 0)) for item in feed.get("items", []) if item.get("number")]
        _, errors = mark_unread_feed_items(feed, item_numbers, "read")
        await refresh_unread_feed_markups(context, feed, query=query)
        if errors:
            await report_unread_action_errors(query, errors)
        else:
            await query.answer()
        return

    if action == "leave":
        await query.answer("Left unread.")
        await refresh_unread_global_markup(context, feed, query=query)
        return

    await query.answer()


async def scan_more_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        return
    if not is_authorized(update):
        await query.answer("Not authorized.", show_alert=True)
        return

    parts = (query.data or "").split(":")
    if len(parts) != 3 or parts[0] != "scan" or parts[1] != "more":
        await query.answer()
        return
    try:
        batch_id = int(parts[2])
    except ValueError:
        await query.answer("That scan page is not valid.", show_alert=True)
        return

    batch = SCAN_BATCHES.get(batch_id)
    if not batch:
        await query.answer("That scan is no longer available.", show_alert=True)
        if query.message:
            await query.edit_message_reply_markup(reply_markup=None)
        return

    start = int(batch.get("offset", 0))
    if start >= len(batch.get("items", [])):
        await query.answer()
        if query.message:
            await query.edit_message_reply_markup(reply_markup=None)
        return

    await query.answer()
    if query.message:
        await query.edit_message_reply_markup(reply_markup=None)
        sent = await send_scan_page(query.message, batch_id, start)
        if not sent:
            await query.message.reply_text("That’s everything new I found for now.", disable_web_page_preview=True)


def parse_pending_index(args: list[str]) -> int | None:
    if not args:
        return None
    try:
        return int(args[0])
    except ValueError:
        return None


async def add_pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    index = parse_pending_index(context.args)
    if index is None:
        await update.message.reply_text("Send the item number you want me to add.")
        return
    try:
        await update.message.reply_text(add_pending_candidate(index)[:4000])
    except Exception as e:
        await update.message.reply_text(f"❌ Calendar error: {e}")


async def skip_pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    index = parse_pending_index(context.args)
    if index is None:
        await update.message.reply_text("Send the item number you want me to leave for later.")
        return
    if index < 1 or index > len(PENDING_EMAIL_APPOINTMENTS):
        await update.message.reply_text("That pending item number is not available.")
        return
    candidate = PENDING_EMAIL_APPOINTMENTS[index - 1]
    if candidate.get("status", "pending") != "pending":
        await update.message.reply_text(handled_candidate_text("add" if candidate.get("status") == "added" else "skip", index))
        return
    candidate["status"] = "skipped"
    remember_gmail_id("skipped", candidate.get("source_id", ""))
    log_event("appointment_skipped", source_id=candidate.get("source_id", ""), title=candidate.get("title", ""))
    return


async def add_all_pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    pending_count = sum(1 for item in PENDING_EMAIL_APPOINTMENTS if item.get("status", "pending") == "pending")
    if not pending_count:
        await update.message.reply_text("No pending email appointments right now.")
        return

    added = []
    for candidate in list(PENDING_EMAIL_APPOINTMENTS):
        if candidate.get("status", "pending") != "pending":
            continue
        validation_error = validate_email_candidate(candidate)
        if validation_error:
            continue
        try:
            event = create_calendar_event(
                candidate["title"],
                candidate_date_text(candidate),
                True,
                "2",
                candidate.get("description", ""),
                60,
                candidate.get("location", ""),
                [60, 1440],
            )
            candidate["status"] = "added"
            remember_gmail_id("calendar_added", candidate.get("source_id", ""))
            log_event("calendar_event_added", source_id=candidate.get("source_id", ""), title=candidate.get("title", ""))
            added.append(f"{event_start_text(event)} — {event.get('summary', candidate['title'])}")
        except Exception as e:
            added.append(f"Could not add {candidate.get('title', 'one item')}: {e}")
    if not added:
        await update.message.reply_text("I could not add any pending items because they need clearer date and time details.")
        return
    kept = [item for item in PENDING_EMAIL_APPOINTMENTS if item.get("status", "pending") == "pending"]
    suffix = f"\n\n{len(kept)} item(s) still need clearer details." if kept else ""
    await update.message.reply_text(("Added:\n" + "\n".join(f"- {item}" for item in added) + suffix)[:4000])


async def clear_pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ Not authorized.")
        return

    count = len(PENDING_EMAIL_APPOINTMENTS)
    PENDING_EMAIL_APPOINTMENTS.clear()
    await update.message.reply_text(f"Cleared {count} pending appointment{'s' if count != 1 else ''}.")


def normalize_intent_text(value: str) -> str:
    text = value.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def pending_candidate_indexes() -> list[int]:
    return [
        index
        for index, candidate in enumerate(PENDING_EMAIL_APPOINTMENTS, start=1)
        if candidate.get("status", "pending") == "pending"
    ]


def number_from_intent(text: str, verbs: tuple[str, ...]) -> int | None:
    for verb in verbs:
        match = re.search(rf"\b{re.escape(verb)}\s+(\d+)\b", text)
        if match:
            return int(match.group(1))
    match = re.search(r"\bappointment\s+(\d+)\b", text)
    if match and any(verb in text for verb in verbs):
        return int(match.group(1))
    return None


def assistant_help_text() -> str:
    return (
        "I can help with:\n"
        "📩 Email — scan, summarize, mark read/unread\n"
        "📅 Calendar — detect appointments and add them with approval\n"
        "🧠 Digests — show what needs attention\n"
        "🔔 Automation — morning/afternoon/evening checks\n"
        "🖥️ System — show bot/server status\n\n"
        "Try saying:\n"
        "\"check my email\"\n"
        "\"anything important?\"\n"
        "\"show my highlights\"\n"
        "\"add that to my calendar\""
    )


def last_relevant_actionable(item_type: str | None = None) -> dict | None:
    for item in reversed(RECENT_ACTIONABLE_ITEMS):
        if item_type and item.get("type") != item_type:
            continue
        if item.get("type") == "appointment":
            index = int(item.get("id", 0))
            if 1 <= index <= len(PENDING_EMAIL_APPOINTMENTS) and PENDING_EMAIL_APPOINTMENTS[index - 1].get("status", "pending") == "pending":
                return item
        if item.get("type") == "email":
            card_id = int(item.get("id", 0))
            if card_id in EMAIL_CARDS and EMAIL_CARDS[card_id].get("status") in {"read", "unread"}:
                return item
    return None


def actionable_indexes(item_type: str) -> list[int]:
    if item_type == "appointment":
        return pending_candidate_indexes()
    if item_type == "email":
        return active_email_card_ids()
    return []


def resolve_action_target(text: str, action: str, item_type: str) -> tuple[int | None, str | None]:
    explicit = number_from_intent(text, (action, "mark", "summarize", "summarise", "skip", "add", "put", "leave"))
    candidates = actionable_indexes(item_type)
    if explicit is not None:
        return explicit, None
    last = last_relevant_actionable(item_type)
    if last and len(candidates) <= 1:
        return int(last["id"]), None
    if len(candidates) == 1:
        return candidates[0], None
    if len(candidates) > 1:
        return None, "I found a few items. Tell me the number you want, or use the buttons."
    return None, "I don’t have a current item for that yet. Ask me to check your email first."


async def add_appointment_from_intent(update: Update, index: int) -> None:
    await update.message.reply_text(add_pending_candidate(index)[:4000], disable_web_page_preview=True)


async def skip_appointment_from_intent(update: Update, index: int) -> None:
    if index < 1 or index > len(PENDING_EMAIL_APPOINTMENTS):
        await update.message.reply_text("That pending item number is not available.")
        return
    candidate = PENDING_EMAIL_APPOINTMENTS[index - 1]
    if candidate.get("status", "pending") != "pending":
        await update.message.reply_text(handled_candidate_text("add" if candidate.get("status") == "added" else "skip", index))
        return
    candidate["status"] = "skipped"
    remember_gmail_id("skipped", candidate.get("source_id", ""))
    mark_scan_item_action_today({"type": "appointment", "index": index}, "skipped")
    log_event("appointment_skipped", source_id=candidate.get("source_id", ""), title=candidate.get("title", ""))
    return


async def email_card_action_from_intent(update: Update, card_id: int, action: str) -> None:
    card = EMAIL_CARDS.get(card_id)
    if not card:
        await update.message.reply_text("I don’t have a current item for that yet. Try asking me to check your email.")
        return
    if action == "read":
        reply = mark_gmail_message_read(card.get("id", ""))
        if reply.startswith("✅"):
            card["status"] = "read"
            remember_gmail_id("read", card.get("id", ""))
            mark_scan_item_status_today(card.get("scan_identity", ""), "read")
            log_event("email_read", message_id=card.get("id", ""))
        else:
            await update.message.reply_text(reply[:4000], disable_web_page_preview=True)
        return
    if action == "unread":
        reply = mark_gmail_message_unread(card.get("id", ""))
        if reply.startswith("✅"):
            card["status"] = "unread"
            remember_gmail_id("unread", card.get("id", ""))
            log_event("email_unread", message_id=card.get("id", ""))
        else:
            await update.message.reply_text(reply[:4000], disable_web_page_preview=True)
        return
    if action == "summarize":
        email_item, error = fetch_gmail_message(card.get("id", ""))
        if error:
            email_item = card
        else:
            card.update(email_item)
        remember_gmail_id("summarized", card.get("id", ""))
        mark_scan_item_status_today(card.get("scan_identity", ""), "summarized")
        log_event("email_summarized", message_id=card.get("id", ""))
        await update.message.reply_text(summarize_email_item(email_item)[:4000], disable_web_page_preview=True)
        return
    if action == "skip":
        card["status"] = "skipped"
        remember_gmail_id("skipped", card.get("id", ""))
        mark_scan_item_status_today(card.get("scan_identity", ""), "skipped")
        log_event("email_skipped", message_id=card.get("id", ""))
        return


async def show_more_from_intent(update: Update) -> None:
    batch_id = latest_scan_batch_id()
    if batch_id is None:
        await update.message.reply_text("I don’t have anything else queued right now.")
        return
    batch = SCAN_BATCHES.get(batch_id, {})
    start = int(batch.get("offset", 0))
    if start >= len(batch.get("items", [])):
        await update.message.reply_text("That’s everything new I found for now.", disable_web_page_preview=True)
        return
    sent = await send_scan_page(update.message, batch_id, start)
    if not sent:
        await update.message.reply_text("That’s everything new I found for now.", disable_web_page_preview=True)


CONTROLLER_INTENTS = {
    "show_unread_emails",
    "show_highlights",
    "run_scan_cards",
    "show_more",
    "summarize_latest_email",
    "mark_latest_read",
    "mark_latest_unread",
    "add_latest_appointment",
    "skip_latest_item",
    "show_calendar",
    "show_status",
    "show_help",
    "reset_brain",
    "general_chat",
    "unknown",
}


def controller_decision(intent: str, confidence: float, reason: str, target=None) -> dict:
    return {
        "intent": intent if intent in CONTROLLER_INTENTS else "unknown",
        "confidence": max(0.0, min(float(confidence), 1.0)),
        "target": target,
        "reason": reason,
    }


def controller_target_number(text: str) -> int | None:
    match = re.search(r"\b(?:email|item|message|number)?\s*(\d{1,2})\b", text)
    if not match:
        return None
    return int(match.group(1))


def controller_has_tool_word(text: str) -> bool:
    return any(
        word in text
        for word in (
            "email",
            "emails",
            "inbox",
            "unread",
            "important",
            "highlight",
            "scan",
            "calendar",
            "appointment",
            "status",
            "read",
            "skip",
            "summarize",
            "summarise",
        )
    )


def deterministic_interpret_user_message(user_text: str, context: dict) -> dict:
    text = normalize_intent_text(user_text)
    target_number = controller_target_number(text)
    if not text:
        return controller_decision("unknown", 0.0, "empty message")

    if text in {"help", "help me", "what can you do", "what do you do", "capabilities", "how can you help"}:
        return controller_decision("show_help", 0.98, "user asked for help")
    if any(phrase in text for phrase in ("reset your brain", "reset brain", "clear your brain", "clear brain")):
        return controller_decision("reset_brain", 0.98, "user asked to reset brain")
    if text in {"status", "show status", "system status"} or any(phrase in text for phrase in ("are you working", "is everything working", "are you online")):
        return controller_decision("show_status", 0.97, "user asked for status")
    if any(phrase in text for phrase in ("show more", "show more emails", "more emails", "next emails", "show 3 more")):
        return controller_decision("show_more", 0.96, "user asked for more items")

    if any(phrase in text for phrase in ("check my email", "check my emails", "check my inbox", "show unread emails", "show unread email", "show me unread emails", "show me the unread ones", "show my unread", "show inbox", "show my inbox", "what emails do i have", "read my email", "read my emails", "summarize my inbox", "summarize my emails", "summarise my emails", "anything new")) and not any(word in text for word in ("important", "urgent", "highlight")):
        return controller_decision("show_unread_emails", 0.96, "user asked to see unread email")
    if any(phrase in text for phrase in ("anything important", "anything urgent", "what should i care about", "what should i do", "what needs my attention", "show important emails", "show urgent emails", "highlights", "show highlights", "show my highlights", "inbox highlights")):
        return controller_decision("show_highlights", 0.95, "user asked for priority email view")
    if any(phrase in text for phrase in ("scan emails", "scan email", "scan my inbox", "action cards", "show cards")):
        return controller_decision("run_scan_cards", 0.94, "user asked for actionable scan cards")

    if any(phrase in text for phrase in ("summarize that", "summarise that", "summarize this", "summarise this", "summarize it", "summarise it", "summarize latest email", "summarise latest email", "what does it say", "summary of that")) or re.search(r"\b(summarize|summarise)\s+\d{1,2}\b", text):
        return controller_decision("summarize_latest_email", 0.92, "user asked to summarize latest email", target_number)
    if any(phrase in text for phrase in ("mark it read", "mark this read", "mark that read", "mark email read", "mark latest read", "mark as read")) or re.search(r"\bmark\s+\d{1,2}\s+read\b", text):
        return controller_decision("mark_latest_read", 0.96, "user clearly asked to mark email read", target_number)
    if any(phrase in text for phrase in ("mark it unread", "mark this unread", "mark that unread", "mark latest unread", "leave it unread", "leave this unread", "mark as unread")) or re.search(r"\bmark\s+\d{1,2}\s+unread\b", text):
        return controller_decision("mark_latest_unread", 0.96, "user clearly asked to mark email unread", target_number)
    if any(phrase in text for phrase in ("add that appointment", "add the appointment", "add latest appointment", "add it to my calendar", "add that to my calendar", "put it on my calendar", "put that on my calendar")) or re.search(r"\b(add|put)\s+\d{1,2}\b", text):
        return controller_decision("add_latest_appointment", 0.95, "user clearly asked to add appointment", target_number)
    if any(phrase in text for phrase in ("skip that", "skip this", "skip it", "skip latest", "dismiss that", "dismiss this", "leave it")) or re.search(r"\b(skip|dismiss)\s+\d{1,2}\b", text):
        return controller_decision("skip_latest_item", 0.86, "user asked to skip latest item", target_number)

    if any(phrase in text for phrase in ("show calendar", "check calendar", "what is on my calendar", "what s on my calendar", "do i have anything today", "what do i have today", "anything today", "my schedule")):
        target = {"days": 1} if "today" in text else None
        return controller_decision("show_calendar", 0.92, "user asked for calendar", target)

    if controller_has_tool_word(text):
        return controller_decision("unknown", 0.45, "tool-related message was ambiguous", target_number)
    return controller_decision("general_chat", 0.88, "no tool intent matched")


def extract_json_object(raw: str) -> dict | None:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end < start:
            return None
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None


def ollama_classify_intent(user_text: str, context: dict) -> dict | None:
    if ollama_cooldown_remaining() > 0:
        return None
    prompt = (
        "Classify this Telegram message for Bubbles. JSON only.\n"
        "Bubbles has email, calendar, status, scan, and help tools. Do not answer the user.\n"
        "Intents: show_unread_emails, show_highlights, run_scan_cards, show_more, summarize_latest_email, "
        "mark_latest_read, mark_latest_unread, add_latest_appointment, skip_latest_item, show_calendar, "
        "show_status, show_help, reset_brain, general_chat, unknown.\n"
        'Return {"intent":"...","confidence":0.0,"target":null,"reason":"..."}.\n'
        f"Context: more={bool(context.get('more_available'))}, latest_email={bool(context.get('latest_email_item'))}, latest_appointment={bool(context.get('latest_appointment_index'))}\n"
        f"Message: {cap_text(user_text, 220)}"
    )
    try:
        response = requests.post(
            OLLAMA_GENERATE_URL,
            json={
                "model": MODEL,
                "prompt": cap_text(prompt, 1200),
                "stream": False,
                "options": {"num_predict": 80, "temperature": 0},
            },
            timeout=OLLAMA_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        mark_ollama_online()
    except requests.Timeout as e:
        mark_ollama_failure(e.__class__.__name__, timed_out=True)
        log_event("ollama_timeout", area="controller", error=e.__class__.__name__, failures=OLLAMA_CONSECUTIVE_FAILURES)
        return None
    except Exception as e:
        mark_ollama_failure(e.__class__.__name__)
        log_event("ollama_error", area="controller", error=e.__class__.__name__)
        return None

    parsed = extract_json_object(parse_ollama_response(response.json()))
    if not isinstance(parsed, dict):
        return None
    intent = str(parsed.get("intent", "unknown")).strip()
    if intent not in CONTROLLER_INTENTS:
        return None
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return controller_decision(
        intent,
        confidence,
        str(parsed.get("reason") or "classified by Ollama"),
        parsed.get("target"),
    )


def interpret_user_message(user_text, context) -> dict:
    deterministic = deterministic_interpret_user_message(user_text, context)
    if deterministic.get("confidence", 0) >= 0.85:
        return deterministic
    ai_decision = ollama_classify_intent(user_text, context)
    if ai_decision and ai_decision.get("confidence", 0) >= 0.55:
        return ai_decision
    return deterministic


def mailman_item_to_email(item: dict) -> dict:
    return {
        "id": item.get("gmail_message_id") or item.get("message_id") or "",
        "sender": item.get("from") or item.get("from_display") or "Unknown",
        "subject": item.get("subject") or "(no subject)",
        "snippet": item.get("highlight") or item.get("why_it_matters") or "",
        "body": item.get("highlight") or item.get("why_it_matters") or "",
        "unread": item.get("unread", True),
    }


def resolve_controller_email_target(target=None) -> tuple[dict | None, str | None]:
    choices = list(CONTROLLER_STATE.get("latest_email_choices") or [])
    target_number = target if isinstance(target, int) else None
    if isinstance(target, str) and target.isdigit():
        target_number = int(target)
    if target_number is not None:
        for choice in choices:
            if choice.get("number") == target_number or choice.get("position") == target_number:
                return {"mailman_item": choice.get("item"), "message_id": choice.get("message_id")}, None
        return None, "I don’t see that email number in the current list."
    if choices and CONTROLLER_STATE.get("latest_email_card_id") is None and len(choices) > 1:
        labels = ", ".join(str(choice.get("number")) for choice in choices if choice.get("number"))
        return None, f"Which email should I use? Send {labels}."
    card_id = CONTROLLER_STATE.get("latest_email_card_id")
    if isinstance(card_id, int) and card_id in EMAIL_CARDS:
        return {"card_id": card_id, "message_id": EMAIL_CARDS[card_id].get("id"), "card": EMAIL_CARDS[card_id]}, None
    item = CONTROLLER_STATE.get("latest_email_item")
    if isinstance(item, dict):
        return {"mailman_item": item, "message_id": item.get("gmail_message_id") or item.get("message_id")}, None
    return None, "I don’t have an email in focus yet."


def controller_target_email_payload(target: dict) -> tuple[dict, str | None]:
    message_id = str(target.get("message_id") or "")
    if message_id:
        email_item, error = fetch_gmail_message(message_id)
        if email_item:
            return email_item, None
        if "mailman_item" not in target:
            return {}, error or "I couldn’t open that email."
    if target.get("card"):
        return target["card"], None
    if target.get("mailman_item"):
        return mailman_item_to_email(target["mailman_item"]), None
    return {}, "I couldn’t find that email."


async def summarize_controller_latest_email(update: Update, target=None) -> None:
    email_target, problem = resolve_controller_email_target(target)
    if problem:
        await update.message.reply_text(problem, disable_web_page_preview=True)
        return
    email_item, error = controller_target_email_payload(email_target)
    if error:
        await update.message.reply_text(error[:4000], disable_web_page_preview=True)
        return
    await update.message.reply_text(summarize_email_item(email_item)[:4000], disable_web_page_preview=True)


async def mark_controller_latest_email(update: Update, unread: bool, target=None) -> None:
    email_target, problem = resolve_controller_email_target(target)
    if problem:
        await update.message.reply_text(problem, disable_web_page_preview=True)
        return
    message_id = str(email_target.get("message_id") or "")
    if not message_id:
        await update.message.reply_text("I don’t have a Gmail message for that email.", disable_web_page_preview=True)
        return
    reply = mark_gmail_message_unread(message_id) if unread else mark_gmail_message_read(message_id)
    if not reply.startswith("✅"):
        await update.message.reply_text(reply[:4000], disable_web_page_preview=True)
        return
    card_id = email_target.get("card_id")
    if isinstance(card_id, int) and card_id in EMAIL_CARDS:
        EMAIL_CARDS[card_id]["status"] = "unread" if unread else "read"
    remember_gmail_id("unread" if unread else "read", message_id)
    await update.message.reply_text("Done.", disable_web_page_preview=True)


async def add_controller_latest_appointment(update: Update) -> None:
    index = CONTROLLER_STATE.get("latest_appointment_index")
    if not isinstance(index, int):
        await update.message.reply_text("I don’t have an appointment in focus. Ask me to scan for action cards first.")
        return
    await add_appointment_from_intent(update, index)


async def skip_controller_latest_item(update: Update, target=None) -> None:
    latest_type = CONTROLLER_STATE.get("latest_item_type")
    if latest_type == "appointment":
        index = CONTROLLER_STATE.get("latest_appointment_index")
        if isinstance(index, int):
            await skip_appointment_from_intent(update, index)
            await update.message.reply_text("Done.", disable_web_page_preview=True)
            return
    if latest_type == "email":
        email_target, problem = resolve_controller_email_target(target)
        if problem:
            await update.message.reply_text(problem, disable_web_page_preview=True)
            return
        card_id = email_target.get("card_id") if email_target else None
        if isinstance(card_id, int):
            await email_card_action_from_intent(update, card_id, "skip")
            await update.message.reply_text("Done.", disable_web_page_preview=True)
            return
        await update.message.reply_text("That email list is read-only. Use scan cards if you want to skip items.", disable_web_page_preview=True)
        return
    await update.message.reply_text("Which item should I skip?", disable_web_page_preview=True)


async def show_more_from_controller(update: Update, context_snapshot: dict) -> None:
    source = context_snapshot.get("more_source")
    if source == "scan":
        await show_more_from_intent(update)
        return
    await update.message.reply_text("I don’t have more items queued right now.", disable_web_page_preview=True)


async def route_controller_intent(update: Update, user_input: str, decision: dict, context_snapshot: dict) -> bool:
    intent = decision.get("intent")
    target = decision.get("target")
    if intent == "show_unread_emails":
        await send_unread_email_summary(update.message)
        return True
    if intent == "show_highlights":
        await update.message.reply_text(build_highlights_digest(), disable_web_page_preview=True)
        return True
    if intent == "run_scan_cards":
        await send_scan_results(update)
        return True
    if intent == "show_more":
        await show_more_from_controller(update, context_snapshot)
        return True
    if intent == "summarize_latest_email":
        await summarize_controller_latest_email(update, target)
        return True
    if intent == "mark_latest_read":
        await mark_controller_latest_email(update, False, target)
        return True
    if intent == "mark_latest_unread":
        await mark_controller_latest_email(update, True, target)
        return True
    if intent == "add_latest_appointment":
        await add_controller_latest_appointment(update)
        return True
    if intent == "skip_latest_item":
        await skip_controller_latest_item(update, target)
        return True
    if intent == "show_calendar":
        days = int(target.get("days", 0)) if isinstance(target, dict) and str(target.get("days", "")).isdigit() else parse_days_from_text(user_input)
        await update.message.reply_text(calendar_summary(days), disable_web_page_preview=True)
        return True
    if intent == "show_status":
        await update.message.reply_text(service_status_text()[:4000], disable_web_page_preview=True)
        return True
    if intent == "show_help":
        await update.message.reply_text(assistant_help_text()[:4000], disable_web_page_preview=True)
        return True
    if intent == "reset_brain":
        reset_ollama_state()
        await update.message.reply_text("Done. I cleared the chat cooldown.", disable_web_page_preview=True)
        return True
    if intent == "unknown" and controller_has_tool_word(normalize_intent_text(user_input)):
        await update.message.reply_text("I can help with that. Do you want unread emails, highlights, scan cards, calendar, or status?", disable_web_page_preview=True)
        return True
    return False


async def route_operator_intent(update: Update, user_input: str) -> bool:
    context_snapshot = controller_context_snapshot()
    decision = interpret_user_message(user_input, context_snapshot)
    log_event("controller_intent", intent=decision.get("intent"), confidence=decision.get("confidence"), reason=decision.get("reason", ""))
    return await route_controller_intent(update, user_input, decision, context_snapshot)


async def route_assistant_intent(update: Update, user_input: str) -> bool:
    text = normalize_intent_text(user_input)
    if not text:
        return False

    help_phrases = ("help", "help me", "what can you do", "what do you do", "capabilities", "how can you help")
    if text in help_phrases or any(phrase in text for phrase in help_phrases[1:]):
        await update.message.reply_text(assistant_help_text()[:4000], disable_web_page_preview=True)
        return True

    reset_phrases = ("reset your brain", "reset brain", "clear your brain", "clear brain")
    if any(phrase in text for phrase in reset_phrases):
        reset_ollama_state()
        await update.message.reply_text("Chat brain cooldown cleared.")
        return True

    status_phrases = ("status", "show status", "system status", "are you working", "is everything working", "are you online")
    if text in status_phrases or any(phrase in text for phrase in status_phrases[1:]):
        await update.message.reply_text(service_status_text()[:4000], disable_web_page_preview=True)
        return True

    if any(phrase in text for phrase in ("show more", "show more emails", "more emails", "next emails")):
        await show_more_from_intent(update)
        return True

    highlights_phrases = (
        "show me my highlights",
        "show my highlights",
        "email highlights",
        "inbox highlights",
        "highlights",
    )
    if any(phrase in text for phrase in highlights_phrases):
        await update.message.reply_text("I’ll pull the latest highlights.", disable_web_page_preview=True)
        await update.message.reply_text(build_highlights_digest(), disable_web_page_preview=True)
        return True

    digest_phrases = (
        "run my digest",
        "run digest",
        "morning digest",
        "afternoon digest",
        "evening digest",
        "evening recap",
        "daily digest",
        "what needs my attention",
    )
    if any(phrase in text for phrase in digest_phrases):
        sent = await run_proactive_digest_message(update.message, "Manual email digest")
        if not sent:
            await update.message.reply_text("No new digest items right now.", disable_web_page_preview=True)
        return True

    scan_phrases = (
        "scan emails",
        "scan email",
        "scan my inbox",
        "what s in my email",
        "whats in my email",
    )
    if any(phrase in text for phrase in scan_phrases):
        await update.message.reply_text("Checking now.", disable_web_page_preview=True)
        await send_scan_results(update)
        return True

    if "what did you find" in text:
        if pending_candidate_indexes():
            await update.message.reply_text(pending_email_summary()[:4000], disable_web_page_preview=True)
        else:
            await update.message.reply_text(build_highlights_digest(), disable_web_page_preview=True)
        return True

    pending_phrases = (
        "any appointments",
        "show pending",
        "show appointments",
        "show pending appointments",
        "appointments",
        "what appointments did you find",
    )
    if any(phrase in text for phrase in pending_phrases):
        await update.message.reply_text(pending_email_summary()[:4000], disable_web_page_preview=True)
        return True

    add_index = number_from_intent(text, ("add", "put"))
    add_phrases = (
        "add it",
        "add this",
        "add that",
        "add that appointment",
        "add the appointment",
        "add it to my calendar",
        "add that to my calendar",
        "put it on my calendar",
        "put that on my calendar",
    )
    if add_index is not None or any(phrase in text for phrase in add_phrases):
        if add_index is None:
            add_index, problem = resolve_action_target(text, "add", "appointment")
            if problem:
                await update.message.reply_text(problem)
                return True
        await add_appointment_from_intent(update, add_index)
        return True

    skip_email_phrases = ("skip this email", "skip that email", "skip email", "dismiss this email")
    if any(phrase in text for phrase in skip_email_phrases):
        card_id, problem = resolve_action_target(text, "skip", "email")
        if problem:
            await update.message.reply_text(problem)
            return True
        await email_card_action_from_intent(update, card_id, "skip")
        return True

    skip_index = number_from_intent(text, ("skip", "dismiss", "leave"))
    skip_phrases = ("skip it", "skip this", "dismiss it", "skip that appointment", "leave that appointment")
    if skip_index is not None or any(phrase in text for phrase in skip_phrases):
        if skip_index is None:
            skip_index, problem = resolve_action_target(text, "skip", "appointment")
            if problem:
                await update.message.reply_text(problem)
                return True
        await skip_appointment_from_intent(update, skip_index)
        return True

    if any(phrase in text for phrase in ("mark it read", "mark this read", "mark that read", "mark email read")):
        card_id, problem = resolve_action_target(text, "read", "email")
        if problem:
            await update.message.reply_text(problem)
            return True
        await email_card_action_from_intent(update, card_id, "read")
        return True

    if any(phrase in text for phrase in ("mark it unread", "mark this unread", "mark that unread", "leave it unread", "leave this unread")):
        card_id, problem = resolve_action_target(text, "unread", "email")
        if problem:
            await update.message.reply_text(problem)
            return True
        await email_card_action_from_intent(update, card_id, "unread")
        return True

    if any(phrase in text for phrase in ("summarize this email", "summarize that email", "summarize it", "summarise it", "summarize this", "summarise this")):
        card_id, problem = resolve_action_target(text, "summarize", "email")
        if problem:
            await update.message.reply_text(problem)
            return True
        await email_card_action_from_intent(update, card_id, "summarize")
        return True

    tool_words = ("email", "inbox", "appointment", "calendar", "digest", "highlight", "read", "unread", "summarize", "summarise")
    if any(word in text for word in tool_words):
        await update.message.reply_text("I can help with that, but I need a clearer action. Try “check my email” or “show my highlights”.")
        return True

    return False


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
    await update.message.reply_text(text[:4000], disable_web_page_preview=True)
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

    if await route_operator_intent(update, user_input):
        remember_message(user.id, "user", user_input)
        return

    if await route_assistant_intent(update, user_input):
        remember_message(user.id, "user", user_input)
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

    mode = configure_ollama_mode()
    mode_label = ollama_mode_label()
    endpoint = OLLAMA_CHAT_URL if mode == "chat" else OLLAMA_GENERATE_URL
    print(f"Ollama model: {MODEL}")
    print(f"Ollama mode: {mode_label}")
    print(f"Ollama endpoint: {endpoint}")
    print(service_status_text(LAST_OLLAMA_HEALTH))
    print(
        "Ollama timeouts: "
        f"connect={OLLAMA_CONNECT_TIMEOUT_SECONDS}s read={OLLAMA_READ_TIMEOUT_SECONDS}s "
        f"cooldown={OLLAMA_COOLDOWN_SECONDS}s history_turns={OLLAMA_MAX_HISTORY_TURNS} "
        f"prompt_chars={OLLAMA_PROMPT_CHAR_LIMIT}"
    )

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(start_background_tasks).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler(["about", "helpme"], about_command))
    app.add_handler(CommandHandler(["ollama", "brain"], ollama_command))
    app.add_handler(CommandHandler("resetbrain", resetbrain_command))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("calendar", calendar_command))
    app.add_handler(CommandHandler("next", next_command))
    app.add_handler(CommandHandler("free", free_command))
    app.add_handler(CommandHandler("scan", scan_command))
    app.add_handler(CommandHandler("highlights", highlights_command))
    app.add_handler(CommandHandler("assistanttest", assistanttest_command))
    app.add_handler(CommandHandler("scheduledtest", scheduledtest_command))
    app.add_handler(CommandHandler("rundigest", rundigest_command))
    app.add_handler(CommandHandler("schedule", schedule_command))
    app.add_handler(CommandHandler("memory", memory_command))
    app.add_handler(CommandHandler("logtail", logtail_command))
    app.add_handler(CommandHandler("workflow", workflow_command))
    app.add_handler(CommandHandler("mailmantest", mailmantest_command))
    app.add_handler(CommandHandler("gmailstatus", gmail_status_command))
    app.add_handler(CommandHandler("debugmail", debugmail_command))
    app.add_handler(CommandHandler("day", day_command))
    app.add_handler(CommandHandler("summary", email_summary_command))
    app.add_handler(CommandHandler("pending", pending_command))
    app.add_handler(CommandHandler("add", add_pending_command))
    app.add_handler(CommandHandler("skip", skip_pending_command))
    app.add_handler(CommandHandler("addall", add_all_pending_command))
    app.add_handler(CommandHandler("clearpending", clear_pending_command))
    app.add_handler(CommandHandler("add_event", add_event_command))
    app.add_handler(CommandHandler("seed_test_events", seed_test_events_command))
    app.add_handler(CommandHandler("calendar_setup", calendar_setup_command))
    app.add_handler(CommandHandler("ls", ls_command))
    app.add_handler(CommandHandler("read", read_command))
    app.add_handler(CommandHandler("write", write_command))
    app.add_handler(CallbackQueryHandler(appointment_callback, pattern=r"^appt:"))
    app.add_handler(CallbackQueryHandler(unread_email_callback, pattern=r"^unread:"))
    app.add_handler(CallbackQueryHandler(email_card_callback, pattern=r"^email:"))
    app.add_handler(CallbackQueryHandler(scan_more_callback, pattern=r"^scan:more:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Bubbles (@bubbles_sys_bot) is running.")
    app.run_polling()


if __name__ == "__main__":
    main()
