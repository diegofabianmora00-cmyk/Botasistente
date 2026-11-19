import json
import os
import re
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


def load_state():
    if not SENT_FILE.exists():
        return {"reminders": set(), "events": {}, "notify_new": False}
    try:
        data = json.loads(SENT_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            # formato antiguo: conservar recordatorios y sembrar eventos sin avisar
            return {"reminders": set(data), "events": {}, "notify_new": False}
        if isinstance(data, dict):
            reminders = data.get("reminders", [])
            if isinstance(reminders, dict):
                reminders = list(reminders.keys())
            if not isinstance(reminders, list):
                reminders = []
            events = data.get("events", {})
            if not isinstance(events, dict):
                events = {}
            notify_new = "events" in data
            return {"reminders": set(reminders), "events": events, "notify_new": notify_new}
    except Exception:
        pass
    return {"reminders": set(), "events": {}, "notify_new": False}


def save_state(reminders: set[str], events: dict) -> None:
    state = {
        "reminders": sorted(reminders),
        "events": events,
    }
    SENT_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def to_datetime(value):
    if value is None:
        return None, None
    dt = value.dt if hasattr(value, "dt") else value
    if isinstance(dt, datetime):
        local = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        return local.astimezone(timezone.utc), local
    # date (all-day)
    if hasattr(dt, "year") and hasattr(dt, "month") and hasattr(dt, "day"):
        try:
            utc = datetime(dt.year, dt.month, dt.day, 23, 59, 59, tzinfo=timezone.utc)
            local = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
            return utc, local
        except Exception:
            return None, None
    return None, None


def fmt_12h(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{h}:{dt.minute:02d} {ampm}"


def fmt_dt(local, kind: str) -> str:
    if kind == "date":
        return f"{local.day:02d}/{local.month:02d}/{local.year} (todo el día)"
    return f"{local.day:02d}/{local.month:02d}/{local.year} {fmt_12h(local)}"


def get_start_info(component):
    val = component.get("DTSTART")
    if not val:
        val = component.get("DTSTAMP")
    if not val:
        return None, None, "date"
    utc, local = to_datetime(val)
    if not utc or not local:
        return None, None, "date"
    raw = val.dt if hasattr(val, "dt") else val
    kind = "datetime" if isinstance(raw, datetime) else "date"
    return utc, local, kind


def get_end_info(component):
    for field in ("DUE", "DTEND", "DTSTART"):
        val = component.get(field)
        if not val:
            continue
        utc, local = to_datetime(val)
        if not utc or not local:
            continue
        raw = val.dt if hasattr(val, "dt") else val
        kind = "datetime" if isinstance(raw, datetime) else "date"
        return utc, local, kind
    return None, None, "date"


def normalize_course(text: str) -> str:
    t = " ".join(str(text).split()).strip()
    if not t:
        return "N/A"
    mappings = {
        "ING-003": "ingles7-9",
    }
    for code, name in mappings.items():
        if re.search(rf"\b{re.escape(code)}\b", t, re.IGNORECASE):
            return name
    if "/" in t:
        parts = [p.strip() for p in t.split("/") if p.strip()]
        if parts:
            t = parts[-1]
    if " - " in t:
        parts = [p.strip() for p in t.split(" - ") if p.strip()]
        for p in reversed(parts):
            if re.search(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]{3,}", p) and not re.fullmatch(r"[A-Z]{2,}-?\d[\w-]*", p):
                t = p
                break
        else:
            t = parts[-1]
    t = re.sub(r"\s*\([^)]*\)\s*$", "", t).strip() or t
    return t


def get_course(component) -> str:
    raw = None
    cats = component.get("CATEGORIES")
    if cats:
        items = cats.cats if hasattr(cats, "cats") else cats
        if isinstance(items, (list, tuple)):
            texts = [str(x).strip() for x in items if str(x).strip()]
            raw = ", ".join(texts) if texts else None
        else:
            text = str(items).strip()
            raw = text or None
    if not raw:
        desc = component.get("DESCRIPTION")
        if desc:
            d = str(desc)
            for line in d.replace("\\n", "\n").splitlines():
                low = line.lower()
                for prefix in ("course:", "curso:", "materia:", "asignatura:"):
                    if low.startswith(prefix):
                        raw = line.split(":", 1)[1].strip() or None
                        break
                if raw:
                    break
    if not raw:
        loc = component.get("LOCATION")
        if loc and str(loc).strip():
            raw = str(loc).strip()
    if not raw:
        return "N/A"
    return normalize_course(raw)


def is_completed(component) -> bool:
    status = str(component.get("STATUS") or "").strip().upper()
    if status in {"CANCELLED", "COMPLETED"}:
        return True
    if component.get("COMPLETED"):
        return True
    pc = component.get("PERCENT-COMPLETE")
    if pc is not None:
        try:
            if int(str(pc)) >= 100:
                return True
        except Exception:
            pass
    summary = str(component.get("SUMMARY") or "")
    description = str(component.get("DESCRIPTION") or "")
    text = f"{summary} {description}".lower()
    if re.search(r"\b(deber[ií]a|should be)\b", text):
        return False
    patterns = [
        r"\by[ao] entregad[oa]\b",
        r"\bentregado\b",
        r"\bentregada\b",
        r"\bsubmitted\b",
        r"\bcompleted\b",
        r"\bcompletado\b",
        r"\bcompletada\b",
        r"\bfinalizado\b",
        r"\bfinalizada\b",
    ]
    return any(re.search(p, text) for p in patterns)


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

    state = load_state()
    reminders = state["reminders"]
    known_events = state["events"]
    notify_new = state["notify_new"]

    now = datetime.now(timezone.utc)
    changed = False
    events_seen = 0
    upcoming = 0
    sent_count = 0

    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        events_seen += 1

        end_utc, end_local, end_kind = get_end_info(component)
        if not end_utc or end_utc <= now:
            continue

        upcoming += 1
        summary = str(component.get("SUMMARY") or "(Sin título)").strip()
        course = get_course(component)
        uid = str(component.get("UID") or "").strip() or f"no-uid::{summary}::{end_utc.isoformat()}"

        start_utc, start_local, start_kind = get_start_info(component)
        if not start_local:
            start_local, start_kind = end_local, end_kind
        if not start_utc:
            start_utc = end_utc

        start_text = fmt_dt(start_local, start_kind)
        end_text = fmt_dt(end_local, end_kind)

        completed = is_completed(component)

        # Avisos discretos: tarea nueva o cambio de fecha de entrega
        prev = known_events.get(uid)
        current_fp = {"start": start_utc.isoformat(), "due": end_utc.isoformat()}
        if prev is None:
            if notify_new and not completed:
                msg = f"ℹ️ Tarea nueva en {course}: {summary}. Tienes del {start_text} hasta el {end_text} para entregarla."
                try:
                    send_telegram(token, chat_id, msg)
                    print(f"Aviso nueva tarea: {summary}")
                    sent_count += 1
                except Exception as e:
                    print(f"Error enviando aviso de nueva tarea '{summary}': {e}", file=sys.stderr)
        else:
            prev_due = prev.get("due")
            if prev_due != current_fp["due"]:
                if notify_new and not completed:
                    msg = f"ℹ️ Fecha actualizada en {course}: {summary}. Ahora tienes del {start_text} hasta el {end_text} para entregarla."
                    try:
                        send_telegram(token, chat_id, msg)
                        print(f"Aviso cambio de fecha: {summary}")
                        sent_count += 1
                    except Exception as e:
                        print(f"Error enviando aviso de cambio '{summary}': {e}", file=sys.stderr)
        if known_events.get(uid) != current_fp:
            known_events[uid] = current_fp
            changed = True

        # Avisos de vencimiento (hoy/mañana)
        if completed:
            print(f"Omitido (entregado/completado): '{summary}'")
            continue

        tz = end_local.tzinfo or timezone.utc
        now_local = now.astimezone(tz)
        today = now_local.date()
        due_day = end_local.date()

        if due_day == today:
            label = "hoy"
            hora = fmt_12h(end_local) if end_kind == "datetime" else "todo el día"
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
            print(f"Sin aviso de vencimiento: '{summary}' vence en {days_left} día(s) ({end_text})")
            continue

        key = f"{uid}|{end_utc.isoformat()}|{label}"
        if key in sent:
            print(f"Ya enviado ({label}): '{summary}'")
            continue
        try:
            send_telegram(token, chat_id, msg)
            print(f"Aviso enviado ({label}): {summary}")
            reminders.add(key)
            changed = True
            sent_count += 1
        except Exception as e:
            print(f"Error enviando aviso para '{summary}': {e}", file=sys.stderr)

    # Guardar estado (migración/baseline incluido)
    save_state(reminders, known_events)

    print(
        f"Resumen: eventos={events_seen}, próximos={upcoming}, avisos_enviados={sent_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
