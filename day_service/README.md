# day-service

Окремий сервіс з єдиною відповідальністю: **знати, коли для кожного
акаунта настав новий день, і один раз про це оповістити**. Нічого не
знає про daily-бонус, mining, quiz, читання тощо — це вже turn бізнес-
застосунку (`app`).

## Свідомо мінімальний ресурсний слід

Сервіс 99.9% часу нічого не робить — тому навмисно **без HTTP-сервера**
(жодного FastAPI/uvicorn/pydantic) і **без окремої asyncio-задачі на
кожен акаунт**:

- Уся взаємодія з `app` — через Redis: список-черга команд
  (`day_service:commands`, `BRPOP`) на вхід, pub/sub-канал
  (`day_service_events:{account_id}`) на вихід. Жодного відкритого порту.
- Один процес, один фоновий цикл: `heapq` з "коли наступний акаунт має
  спрацювати", `asyncio.sleep` рівно до найближчої події. Реєстрація/
  видалення акаунта лише будить цей цикл, а не створює/скасовує окрему
  задачу — O(log N) на операцію замість N паралельних "сплячих" тасків.
- Єдина залежність — `redis` (asyncio-клієнт). SQLite — стандартна
  бібліотека.

Орієнтовний RSS у спокої: **20-30 МБ** (проти ~50-70 МБ з
FastAPI+uvicorn+pydantic+httpx), CPU — практично 0 (весь час у
`BRPOP`/`asyncio.sleep`, прокидання раз на акаунт на день).

## Стійкість до перезапуску

Кожне спрацювання спершу записується в SQLite (`day_runs`, PK
`(account_id, day)`) і лише ПІСЛЯ успішного запису публікується подія в
Redis. Тому:

- Швидкий рестарт сервісу в межах того самого дня НЕ призводить до
  повторного оповіщення (запис вже є в `day_runs`).
- Якщо сервіс не працював рівно в момент, коли мав спрацювати
  будильник акаунта (простій, деплой) — при старті це виявляється і
  подія "доганяючи" публікується один раз негайно.

## Протокол (Redis, без HTTP)

Команди — `LPUSH` у список `day_service:commands` (день-сервіс читає
через `BRPOP`, тому команди не губляться, навіть якщо сервіс на мить
недоступний):

```json
{"action": "register",   "account_id": "acc1", "base_time": "04:30", "jitter_minutes": 180}
{"action": "unregister", "account_id": "acc1"}
{"action": "status",     "account_id": "acc1", "reply_to": "day_service:reply:<uuid>"}
{"action": "list",       "reply_to": "day_service:reply:<uuid>"}
{"action": "force",      "account_id": "acc1", "reply_to": "day_service:reply:<uuid>"}
```

`register`/`unregister` — fire-and-forget (`reply_to` не потрібен, той
самий контракт, що й раніше: помилка реєстрації не блокує роботу app).
`status`/`list`/`force` — опційно з `reply_to`: відповідь приходить через
`LPUSH` у той ключ (клієнт читає `BLPOP` з таймаутом), ключ сам стирається
через `EXPIRE` за 30с, якщо ніхто не забрав.

`base_time`/`jitter_minutes` опційні — якщо не передані, беруться
дефолти з env (`DAY_SERVICE_DEFAULT_BASE_TIME`,
`DAY_SERVICE_DEFAULT_JITTER_MINUTES`), ідентичні до колишнього
`DailyMonitor.BASE_TIME = "04:30"` + `jitter_minutes=180`.

Подія "новий день" (pub/sub, `day_service_events:{account_id}`):

```json
{"account_id": "acc1", "event": "new_day", "data": {"day": "2026-07-18"}}
```

Формат ідентичний до того, як account-service публікує socket-події
(`account_events:{account_id}`) — на боці `app` слухач
(`src/core/day_events.py`) побудований за тим самим патерном.

## Healthcheck без HTTP

`main.py` торкається файла-"пульсу" (`/tmp/day_service_heartbeat`) після
кожної успішної спроби зв'язку з Redis (сам `BRPOP`, з командою чи без).
`Dockerfile` перевіряє лише "свіжість" mtime цього файла (< 30с) — без
жодних додаткових пакетів, чистий `python3 -c ...`.

## Запуск

```bash
docker compose up --build day-service
```

Або локально:

```bash
pip install -e . --break-system-packages
REDIS_URL=redis://localhost:6379/0 DAY_SERVICE_DB_PATH=./data/day_service.db \
    python3 -m day_service.main
```
