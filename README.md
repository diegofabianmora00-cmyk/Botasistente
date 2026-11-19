# Avisos de tareas del aula por Telegram (Moodle)

Descarga un calendario `.ics` (Moodle) y envía avisos por Telegram mediante GitHub Actions cada 30 minutos:
- **Vencimientos:** recordatorio si una tarea vence **hoy** (con hora en formato 12 h AM/PM) o **mañana**.
- **Cambios en tareas (discreto):** avisa cuando se detecta una **tarea nueva** o cuando se **edita la fecha de entrega** (mensaje tipo: “tarea nueva en X materia, tienes del X hasta el X para entregarla”).
Guarda el estado en `sent.json` para no repetir avisos.

## Requisitos
- Repositorio **privado** en GitHub.
- Secretos en `Settings > Secrets and variables > Actions`:
  - `ICS_URL`: URL del calendario iCal de Moodle.
  - `TELEGRAM_TOKEN`: token del bot de Telegram.
  - `CHAT_ID`: ID del chat donde enviar avisos.

## Archivos
- `check_deadlines.py`: lógica principal.
- `requirements.txt`: dependencias (`icalendar`, `requests`).
- `sent.json`: estado de avisos enviados y tareas conocidas (se actualiza por el workflow).
- `.github/workflows/check_deadlines.yml`: ejecución cada 30 min y manual.

## Ejecución
- Automática: cron `*/30 * * * *` (UTC).
- Manual: pestaña **Actions** > workflow > **Run workflow**.

## Notas
- Los secretos se leen solo de variables de entorno; no se escriben en el código.
- El workflow hace commit de `sent.json` cuando hay cambios para conservar el estado entre ejecuciones.
- No se avisa de tareas marcadas como completadas/entregadas cuando esa información viene en el `.ics`; si Moodle no la incluye, no es detectable automáticamente.
- En la primera ejecución tras actualizar a esta versión se registra el estado actual de las tareas sin enviar avisos masivos de “tarea nueva”.
