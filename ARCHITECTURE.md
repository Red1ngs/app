# Docker + account-service — що зроблено

## Структура репозиторію

```
.
├── docker-compose.yml       ← app + account-service + redis
├── Dockerfile                ← образ бізнес-застосунку (app)
├── .env.example
├── main.py, pyproject.toml, src/   ← бізнес-застосунок (як і раніше)
└── account_service/          ← НОВИЙ сервіс, окремий образ, окрема БД
    ├── Dockerfile
    ├── pyproject.toml
    └── account_service/
        ├── main.py            (FastAPI)
        ├── manager.py         (AccountManager — реєстр живих сесій)
        ├── session.py         (LiveSession: http+auth+socket+msg, без бізнес-методів)
        ├── db.py, repository.py   (власна SQLite: accounts, sessions)
        ├── redis_bus.py       (publish socket-подій в Redis)
        └── transport/         (config_bot, http_client, bot_auth, request_headers,
                                 proxy_queue, parser(auth-only), socket/*)
```

## Що переїхало в account-service

Усе, що раніше жило в `src/mangabuff/session/` (http_client.py, bot_auth.py,
request_headers.py, socket/bot_socket.py, socket/message_socket.py,
socket/ws_common.py) і `src/core/runtime/proxy_queue.py`-механіка виконання
запитів — тепер там. **Своя SQLite БД** (`accounts`, `sessions`) — жодних
бізнес-таблиць (mangas/chapters/inventory лишились у бізнес-сервісі).

## Порт для інших сервісів

Два способи взаємодії, як і просилось:

1. **"Зроби запит"** — `POST /accounts/{id}/request` (generic: method, url,
   room, priority, params/data/json, headers). Бізнес-сервіс каже, що саме
   зробити, account-service виконує через сесію потрібного акаунта.
2. **"Калбек для socket"** — account-service публікує кожну сиру socket-подію
   в Redis-канал `account_events:{account_id}` (готове рішення — Redis
   pub/sub, а не власний протокол). Бізнес-сервіс підписується локальними
   callback'ами через `src/core/account_events.py::account_event_bus`.

Клієнтська бібліотека-порт для бізнес-сервісу: `src/core/account_client.py`
(`AccountServiceClient`, синглтон `account_client`).

## Що НЕ довелось міняти в бізнес-логіці

`mangabuff/mining/*`, `quiz/*`, `daily/*`, `reader/*` — жодних змін. Вони й
далі викликають `bot.safe_session.mine(...)`, `.quiz_start(...)` і т.д. з тими
самими сигнатурами. Змінилась лише *реалізація* `BotSession` (тепер
делегує в account-service замість власного curl_cffi-клієнта) —
`src/mangabuff/session/bot_session.py`.

`SchedulerService`, `EventDrivenScheduler`, `StartupManager` — без змін
(як і раніше будують `Account(account_id, auth, network, app_cfg, repo)`;
всередині `Account.connect()` тепер сам іде в account-service).

## Що змінилось у `core_account.py`

`Account.connect()` тепер:
1. `account_client.register(...)` — ідемпотентно шле email/password/proxy
   в account-service (пароль/проксі й далі беруться з `.env`, як раніше —
   джерело правди не переїхало).
2. `account_client.connect(...)` — просить залогінитись там.
3. Створює легкий `BotSession`-фасад локально (без HTTP/socket) з отриманим
   `user_id`/`user_name`.

## Відома спрощення (варто мати на увазі)

- `AuthService.on_auth_success` раніше викликався після **кожного**
  `check_auth()` (тобто й після кожного re-login всередині сесії).
  Тепер account-service ре-логінить самостійно і не сповіщає бізнес-сервіс
  про кожен цикл — колбек викликається один раз, одразу після `connect()`.
  Якщо потрібна актуальність user_name/is_banned у реальному часі — можна
  або (а) публікувати в Redis подію `session_refreshed` з account-service
  при кожному re-login і підписатись на неї так само, як на socket-події,
  або (б) періодично викликати `GET /accounts/{id}`.
- `is_banned` після `connect()` зараз завжди `False` — account-service поки
  не віддає цей прапорець у відповіді `/connect`. Легко додати (парсер уже
  вміє `parse_is_banned`, просто не прокинутий у payload).
- Таблиця `sessions` лишилась (невикористовуваною) у DDL бізнес-сервісу —
  видалення відкладено, щоб не ускладнювати міграцію наявних БД.

## Запуск

```bash
cp .env.example .env      # заповнити токен бота, admin ids
docker compose up --build
```

`app.yaml` монтується як volume (`./app.yaml:/app/app.yaml:ro`) — покласти
його поруч з `docker-compose.yml`, як і раніше.

## Кілька сервісів на одному акаунті

Якщо другий сервіс (наприклад `queue_service/` — приклад-скафолд у репо)
працює з ТИМИ САМИМИ акаунтами, що й `app`, а не своїми окремими —
`AccountManager.request()` і `.open_dialog()` в account-service серіалізують
`use_room(...) + запит` під `asyncio.Lock` на `account_id`
(`_room_lock_for`). Без цього два клієнти могли б перемкнути socket-room
одне під одним посеред запиту й отримати чужу відповідь. Плата за це —
запити різних сервісів на одному акаунті чекають один одного в черзі
(не паралеляться) — прийнятно для помірного навантаження, але тримати в
голові при масштабуванні великої кількості сервісів на малу кількість
спільних акаунтів.

## Наступні кроки (навмисно НЕ зроблено зараз)

- Шифрування password у власній БД account-service (зараз plaintext, як
  було і раніше в `.env`/бізнес-БД — паритет, не регрес, але вартий уваги).
- Публікація `session_refreshed`/`is_banned` подій (див. вище).
- Автентифікація/мережева ізоляція account-service (зараз довіряємо
  внутрішній docker-мережі; порт 8100 відкритий назовні лише для дебагу —
  прибрати `ports:` в compose, якщо не потрібно).
