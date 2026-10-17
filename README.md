# PulseNotify - Flight Price Alert Backend

Django REST Framework + JWT + Celery (Beat) + Redis + PostgreSQL.

## Quick start
```bash
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                 # then edit values
docker compose up -d                                 # PostgreSQL + Redis
python manage.py migrate
python manage.py create_pulse_admin                  # admin: pulseadmin / AdminPass123!
```

Run three processes (three terminals):
```bash
python manage.py runserver
celery -A pulsenotify worker --loglevel=info         # Windows: add  --pool=solo
celery -A pulsenotify beat --loglevel=info
```
- Worker terminal: `send_notification` tasks arrive.
- Beat terminal: `check_prices` fires every 60 s.

## Tests
```bash
python manage.py test                 # uses PostgreSQL from .env
USE_SQLITE=1 python manage.py test    # no Docker needed
```

## Endpoints
| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | /api/auth/register/ | none | 201 + JWT, role always `user`, 400 on duplicate |
| POST | /api/auth/login/ | none | 200 + JWT, 401 invalid |
| POST | /api/alerts/ | JWT | 201, user taken from token |
| GET | /api/alerts/ | JWT | own alerts only, `[]` when empty |
| DELETE | /api/alerts/<id>/ | JWT | sets `inactive`; 404 if missing or not yours |
| GET | /api/flights/price/?route=DEL-BOM | none | mock feed; 404 unknown route |
| GET | /api/admin/summary/ | JWT admin | 403 for regular users |

## Postman
Import `postman/PulseNotify.postman_collection.json`. Run in order (13 scenarios, each with test assertions).
- Request 12 needs an admin: `python manage.py create_pulse_admin`.
- Request 13 (NotificationLog) needs all 3 processes running and ~60 s after request 6. For the PGAdmin check:
  `SELECT * FROM pulse_notificationlog ORDER BY notified_at DESC;`
- Tip: to see a notification fire, create an alert (req 6, threshold 7000 always fires on DEL-BOM) and wait for Beat BEFORE running req 8 (which deactivates it).

## Project layout
```
pulsenotify/            settings/{base,local,production}.py, celery.py, urls.py
pulse/                  models, signals, serializers, views, permissions, tasks, tests
postman/                collection JSON
docker-compose.yml      postgres + redis
```
Never commit `.env`. Switch to production with `DJANGO_SETTINGS_MODULE=pulsenotify.settings.production`.
