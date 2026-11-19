import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from icalendar import Calendar

SENT_FILE = Path("sent.json")
WINDOWS = {
    "24h": (23 * 60 + 30, 24 * 60 + 30),  # minutes remaining
    "3h": (2 * 60 + 30, 3 * 60 + 30),
}


def env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"Falta la variable de entorno: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def load_sent() -> set[str]:
    if not SENT_FILE.exists():
        return set()
    try:
        data = json.loads(SENT_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return set(data)
        if isinstance(data, dict):
            return set(data.keys())
    except Exception:
        return set()
    return set()


def save_sent(sent: set[str]) -> None:
    SENT_FILE.write_text(
        json.dumps(sorted(sent), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def to_datetime(value) -> datetime | None:
    if value is None:
        return None
    dt = value.dt if hasattr(value, "dt") else value
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    # date (all-day): treat as end of day UTC for comparison
    if hasattr(dt, "year") and hasattr(dt, "month") and hasattr(dt, "day"):
        try:
            return datetime(dt.year, dt.month, dt.day, 23, 59, 59, tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def get_due(component) -> datetime | None:
    for field in ("DUE", "DTEND", "DTSTART"):
        val = component.get(field)
        dt = to_datetime(val)
        if dt:
            return dt
    return None


def get_course(component) -> str:
    cats = component.get("CATEGORIES")
    if cats:
        items = cats.cats if hasattr(cats, "cats") else cats
        if isinstance(items, (list, tuple)):
            text = ", ".join(str(x) for x in items if x)
            if text.strip():
                return text.strip()
        text = str(items).strip()
        if text:
            return text
    desc = component.get("DESCRIPTION")
    if desc:
        d = str(desc)
        for line in d.replace("\\n", "\n").splitlines():
            low = line.lower()
            for prefix in ("course:", "curso:", "materia:", "asignatura:"):
                if low.startswith(prefix):
                    return line.split(":", 1)[1].strip() or "N/A"
    loc = component.get("LOCATION")
    if loc and str(loc).strip():
        return str(loc).strip()
    return "N/A"


def format_due(component, due_utc: datetime) -> str:
    # Prefer original timezone for display if available
    for field in ("DUE", "DTEND", "DTSTART"):
        val = component.get(field)
        if not val:
            continue
        dt = val.dt if hasattr(val, "dt") else val
        if isinstance(dt, datetime):
            tzname = dt.tzname() or "UTC"
            return dt.strftime(f"%Y-%m-%d %H:%M {tzname}")
        if hasattr(dt, "year"):
            return f"{dt} (todo el día)"
    return due_utc.strftime("%Y-%m-%d %H:%M UTC")


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": chat_id, "text": text},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")


def main() -> int:
    ics_url = env("ICS_URL")
    token = env("TELEGRAM_TOKEN")
    chat_id = env("CHAT_ID")

    resp = requests.get(ics_url, timeout=30)
    resp.raise_for_status()
    cal = Calendar.from_ical(resp.content)

    now = datetime.now(timezone.utc)
    sent = load_sent()
    changed = False

    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        due = get_due(component)
        if not due or due <= now:
            continue

        uid = str(component.get("UID") or "").strip()
        if not uid:
            summary = str(component.get("SUMMARY") or "evento").strip()
            uid = f"no-uid::{summary}::{due.isoformat()}"

        minutes_left = (due - now).total_seconds() / 60.0
        summary = str(component.get("SUMMARY") or "(Sin título)").strip()
        course = get_course(component)
        due_text = format_due(component, due)

        for label, (low, high) in WINDOWS.items():
            if not (low <= minutes_left <= high):
                continue
            key = f"{uid}|{due.isoformat()}|{label}"
            if key in sent:
                continue
            label_text = "24 horas" if label == "24h" else "3 horas"
            msg = (
                f"⏰ Recordatorio: vence en ~{label_text}\n"
                f"Tarea: {summary}\n"
                f"Materia: {course}\n"
                f"Entrega: {due_text}"
            )
            try:
                send_telegram(token, chat_id, msg)
                print(f"Aviso enviado ({label}): {summary}")
                sent.add(key)
                changed = True
            except Exception as e:
                print(f"Error enviando aviso para '{summary}': {e}", file=sys.stderr)

    if changed:
        save_sent(sent)
    else:
        # ensure file exists for workflow consistency
        if not SENT_FILE.exists():
            save_sent(sent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
