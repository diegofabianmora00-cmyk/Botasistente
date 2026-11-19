import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from icalendar import Calendar

SENT_FILE = Path("sent.json")


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


def get_due_info(component):
    for field in ("DUE", "DTEND", "DTSTART"):
        val = component.get(field)
        if not val:
            continue
        raw = val.dt if hasattr(val, "dt") else val
        due_utc = to_datetime(val)
        if not due_utc:
            continue
        if isinstance(raw, datetime):
            local = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
            tzname = raw.tzname() or "UTC"
            display = raw.strftime(f"%Y-%m-%d %H:%M {tzname}")
            return due_utc, local, display, "datetime"
        if hasattr(raw, "year") and hasattr(raw, "month") and hasattr(raw, "day"):
            local = datetime(raw.year, raw.month, raw.day, tzinfo=timezone.utc)
            display = f"{raw} (todo el día)"
            return due_utc, local, display, "date"
    return None, None, None, None


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


# format_due ya no es necesario; se usa display desde get_due_info


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
    events_seen = 0
    upcoming = 0
    sent_count = 0

    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        events_seen += 1
        due_utc, due_local, due_text, kind = get_due_info(component)
        if not due_utc or due_utc <= now:
            continue

        upcoming += 1
        uid = str(component.get("UID") or "").strip()
        if not uid:
            summary = str(component.get("SUMMARY") or "evento").strip()
            uid = f"no-uid::{summary}::{due_utc.isoformat()}"

        summary = str(component.get("SUMMARY") or "(Sin título)").strip()
        course = get_course(component)

        tz = due_local.tzinfo or timezone.utc
        now_local = now.astimezone(tz)
        today = now_local.date()
        due_day = due_local.date()

        if due_day == today:
            label = "hoy"
            hora = due_local.strftime("%H:%M") if kind == "datetime" else "todo el día"
            msg = (
                f"⏰ Recordatorio\n"
                f"Tarea: {summary}\n"
                f"Materia: {course}\n"
                f"Esta tarea vence hoy a las {hora}."
            )
        elif due_day == today + timedelta(days=1):
            label = "manana"
            msg = (
                f"⏰ Recordatorio\n"
                f"Tarea: {summary}\n"
                f"Materia: {course}\n"
                f"Esta tarea: {summary}, vence mañana."
            )
        else:
            days_left = (due_day - today).days
            print(f"Sin aviso: '{summary}' vence en {days_left} día(s) ({due_text})")
            continue

        key = f"{uid}|{due_utc.isoformat()}|{label}"
        if key in sent:
            print(f"Ya enviado ({label}): '{summary}'")
            continue
        try:
            send_telegram(token, chat_id, msg)
            print(f"Aviso enviado ({label}): {summary}")
            sent.add(key)
            changed = True
            sent_count += 1
        except Exception as e:
            print(f"Error enviando aviso para '{summary}': {e}", file=sys.stderr)

    if changed:
        save_sent(sent)
    else:
        # ensure file exists for workflow consistency
        if not SENT_FILE.exists():
            save_sent(sent)

    print(
        f"Resumen: eventos={events_seen}, próximos={upcoming}, avisos_enviados={sent_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
