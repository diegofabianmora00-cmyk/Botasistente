# Avisos de tareas del aula por Telegram (Moodle)

Descarga un calendario `.ics` (Moodle), detecta tareas que vencen en ~24 h y ~3 h y envía un aviso por Telegram mediante GitHub Actions cada 30 minutos. Guarda los avisos enviados en `sent.json` para no repetirlos.

## Requisitos
- Repositorio **privado** en GitHub.
- Secretos en `Settings > Secrets and variables > Actions`:
  - `ICS_URL`: URL del calendario iCal de Moodle.
  - `TELEGRAM_TOKEN`: token del bot de Telegram.
  - `CHAT_ID`: ID del chat donde enviar avisos.

## Archivos
- `check_deadlines.py`: lógica principal.
- `requirements.txt`: dependencias (`icalendar`, `requests`).
- `sent.json`: registro de avisos ya enviados (se actualiza por el workflow).
- `.github/workflows/check_deadlines.yml`: ejecución cada 30 min y manual.

## Ejecución
- Automática: cron `*/30 * * * *` (UTC).
- Manual: pestaña **Actions** > workflow > **Run workflow**.

## Notas
- Los secretos se leen solo de variables de entorno; no se escriben en el código.
- El workflow hace commit de `sent.json` cuando hay cambios para conservar el estado entre ejecuciones.
