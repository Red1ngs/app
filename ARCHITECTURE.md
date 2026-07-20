# Docker + account-service — що зроблено

## Структура репозиторію

```
.                                 ← цей репо (app)
├── docker-compose.yml       ← app + account-service + card-evaluation +
│                                trade-helper (api/telegram) + redis, усі
│                                разом, в одній мережі, спільні для всіх
├── Dockerfile                ← образ бізнес-застосунку (app)
├── .env.example
└── main.py, pyproject.toml, src/   ← бізнес-застосунок (як і раніше)

../account-service/          ← ОКРЕМИЙ репо-сиблінг (не тут), окремий
│                                образ, окрема БД. Спільний для app і
│                                trade-helper — обидва звертаються до
│                                ОДНОГО й того самого контейнера.
└── service/
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

Якщо другий сервіс (наприклад `card-evaluation`, коли йому задано
`MB_ACCOUNT_ID`) працює з ТИМИ САМИМИ акаунтами, що й `app`, а не своїми
окремими — `AccountManager.request()` і `.open_dialog()` в account-service
серіалізують
`use_room(...) + запит` під `asyncio.Lock` на `account_id`
(`_room_lock_for`). Без цього два клієнти могли б перемкнути socket-room
одне під одним посеред запиту й отримати чужу відповідь. Плата за це —
запити різних сервісів на одному акаунті чекають один одного в черзі
(не паралеляться) — прийнятно для помірного навантаження, але тримати в
голові при масштабуванні великої кількості сервісів на малу кількість
спільних акаунтів.

## day-service — оголошення "настав новий день" (окремий сервіс)

Раніше `DailyMonitor` сам рахував собі, коли завтра "прокинутись"
(`BASE_TIME="04:30"` + стабільний по `md5(account_id)` jitter ±180хв) і
сам собі планував `asyncio.sleep(...)` до цього моменту. Тепер це винесено
в окремий сервіс `day_service/`.

Свідомо **без HTTP-сервера** (жодного FastAPI/uvicorn/pydantic) — сервіс
99.9% часу спить, тож тримати заради нього web-стек недоцільно. Уся
взаємодія — через Redis: список команд (`day_service:commands`, `BRPOP`)
на вхід, pub/sub (`day_service_events:{account_id}`) на вихід. Усередині
сервісу — не N asyncio-тасків (по одному на акаунт), а один `heapq`-цикл
на весь сервіс, що спить рівно до найближчого спрацювання. Орієнтовний
RSS у спокої — 20-30 МБ, CPU — практично 0. Деталі й цифри порівняння —
`day_service/README.md`.

- **day-service** знає ТІЛЬКИ "для account_id настав новий день" (той
  самий hash-jitter алгоритм — вже наявні акаунти спрацьовують у той
  самий час, що й раніше) і публікує це в Redis. Жодного знання про
  daily-бонус/mining/quiz.
- Кожне спрацювання спершу пишеться в SQLite (`day_runs`, PK
  `(account_id, day)`), і лише ПІСЛЯ успішного запису публікується подія
  — тому рестарт day-service у межах того самого дня НЕ шле повторне
  оповіщення. Якщо ж сервіс простояв і пропустив момент — при старті це
  виявляється і подія "доганяючи" публікується один раз негайно.
- **DayAnnouncerService** (`src/core/day_announcer_service.py`) — новий
  CoreService, побудований за ТИМ САМИМ патерном, що вже наявний
  `SocketService` (bind()/unbind(), реєстрація в `mangabuff/setup.py` →
  `register_core_services()`): реєструє акаунт у day-service при
  `bind(bot)` і перекидає його подію "новий день" у глобальний
  scheduler-bus як `daily.new_day`.
- **DailyMonitor** (`src/mangabuff/daily/daily_monitor.py`) більше не
  містить жодного планування "на завтра" — він підписується на
  `daily.new_day` і за нею намагається зібрати бонус. Якщо спроба
  невдала — короткий self-retry (5 хв), а не чекання наступного дня.
- **Порядок "спочатку денний бонус, потім інше" для кожного акаунта**:
  `DailyMonitor` реально емітить `daily.claimed` лише в момент, коли
  звичайний бонус щойно зібрано на сьогодні (раніше цей emit існував у
  коді, але жодного разу не викликався — задокументована, але непрацююча
  подія, прибрано разом з рештою мертвого коду нижче). `MiningMonitor`,
  `QuizMonitor` (mode="daily") і `ReadingMonitor` вже були підписані на цю
  подію для скидання лічильників на новий день; тепер вони ще й НЕ
  стартують свій перший цикл на акаунті, поки бонус за сьогодні не
  зібрано (якщо на акаунті взагалі є профессія "daily" — якщо немає, вони
  не чекають нічого і стартують як і раніше). `MiningMonitor._start_mining()`
  при `attach()` також виправлено: раніше запускався безумовно одразу при
  підключенні акаунта, тепер — тільки якщо бонус вже зібрано (або daily не
  підключено), інакше чекає той самий `daily.claimed`. Заразом виправлено
  сам `_waiting_for_daily()` (перейменовано на `_daily_bonus_ready()`):
  для акаунтів БЕЗ профессії "daily" він помилково повертав False, через
  що шахта на таких акаунтах не стартувала НІКОЛИ.

Деталі, протокол, healthcheck без HTTP — `day_service/README.md`.

## Прибраний мертвий код

- `src/mangabuff/daily/daily_monitor.py`: увесь блок самостійного
  планування "на завтра" (`BASE_TIME`, `_calculate_delay`,
  `_get_scheduled_time_str`, `_target_time_today`, `_delay_until_tomorrow`,
  `_delay_when_time_passed`) — замінений day-service (вище).
- `src/utils/time.py`: 15 функцій, які ніде в репозиторії не викликались
  (перевірено `grep` по всьому дереву): `seconds_until_tomorrow_time`,
  `seconds_until_tomorrow_time_stable` (і залежний від неї
  `seconds_until_midnight`), `next_timestamp_for_time`,
  `next_day_timestamp_for_time`, `get_stable_random_time`, `_parse_hh_mm`,
  `compare_dates`, `is_before`, `is_after`, `is_equal`, `is_between`,
  `time_diff`, `shift_date`, `reformat_date`, `format_duration`,
  `tomorrow`. Разом з ними — імпорти `hashlib`, `random`, `Literal`, які
  використовувались лише в цих функціях.
- `SocketService` (`src/mangabuff/session/socket/socket_service.py`) —
  **НЕ видалено**, але варто знати: клас повністю готовий (bind/unbind,
  мапа `_SOCKET_TO_BUS`), проте ніде не зареєстрований через
  `add_core_service()` і жоден монітор/профессія не підписаний на жодну
  `socket.*` подію з `bot.event_bus` — тобто зараз це неактивний код,
  підготовлений про запас. Залишено як є (схоже на свідому заготовку під
  майбутню фічу, а не на випадкове сміття) — вирішувати видаляти чи
  доробляти краще власнику фічі.

## telegram-service — адмінський Telegram-бот (окремий сервіс)

Третій крок декомпозиції (після `account-service` і `day-service` вище):
адмінський Telegram-бот (`aiogram`), що раніше жив як daemon-thread
усередині цього ж монолуту (`AdminBotRunner`, той самий процес, прямі
виклики `SchedulerService` "в пам'яті"), тепер — окремий образ
`telegram_service/` (сиблінг-директорія в корені ЦЬОГО репо, не окремий
git, на відміну від `account-service`).

**Протокол — generic HTTP RPC, той самий стиль, що вже є для
account-service** (`POST /accounts/{id}/request`), а не десятки окремих
REST-ручок:

```
telegram_service.core_client.CoreServiceClient
        │  POST {CORE_SERVICE_URL}/rpc/{method}
        │  body: {"args": [...], "kwargs": {...}}
        ▼
core-service:  src/core/rpc/server.py  →  SchedulerService.<method>(...)
```

- `src/core/services/scheduler_service.py` (перенесено з
  `src/bot/services/` — колишньої "bot"-теки більше немає) лишається
  ЄДИНОЮ бізнес-фасадною поверхнею — так само, як і раніше, коли її
  викликав локальний потік бота.
- `src/core/rpc/server.py` — тонкий FastAPI-шар з explicit whitelist'ом
  методів (`ALLOWED_METHODS`) і bearer-токеном (`CORE_SERVICE_TOKEN`).
  Жодного `getattr` навмання по всьому об'єкту — лише перелічені методи,
  щоб приватні (`_register`) чи несеріалізовні (`get_bot`, повертає
  живий `Account`) не потрапили в мережу через typo на клієнті.
- `telegram_service/core_client.py::CoreServiceClient` навмисно
  повторює сигнатури `SchedulerService` 1:1 — тому всі роутери
  (`routers/accounts/*`, `routers/stats.py`, `routers/logs.py`)
  перенесені практично без змін: `data["svc"]` як був "об'єктом з
  такими методами", так і лишився.
- Дві прямі "дірки" в інкапсуляції, якими роутери користувались, поки
  жили в тому самому процесі (`svc._repo.accounts.get_by_email(...)` і
  `(await svc.get_bot(id)).error`), закриті чистими RPC-safe методами —
  `find_account_by_email()` і `get_account_error()`. Так само `/logs`
  раніше читав файли з диска напряму (`LogReader`, спільна файлова
  система в одному контейнері) — тепер це `logs_list_accounts`/
  `logs_tail_account`/`logs_tail_scheduler`/`logs_errors` через RPC:
  telegram-service і core-service НЕ ділять volume з логами.
- Побічний ефект переносу `routers/logs.py`: у старій версії там
  випадково існували два хендлери на `Command("logs")` — перший (без
  аргументів) завжди перехоплював виклик першим, тому текстові
  `/logs errors` / `/logs scheduler` / `/logs account <id>` фактично
  ніколи не спрацьовували (працювали лише inline-кнопки). Об'єднано в
  один хендлер, що реально розбирає аргументи.

### Контейнеризація й образи

- **core-service** (`Dockerfile`) — multi-stage: build-стадія з
  git/ssh/build-essential (потрібні ЛИШЕ для приватної залежності
  `account-service-client`) збирає venv у `/opt/venv`; runtime-стадія
  — чистий `python:3.13-slim` без цих інструментів, non-root
  (`appuser`, uid 10001), healthcheck по `GET /health` (RPC-порт 8200).
  Раніше git/ssh/build-essential лишались і в фінальному образі.
- **telegram-service** (`telegram_service/Dockerfile`) — одна легка
  стадія: жодних приватних git-залежностей і build-tools взагалі не
  потрібно (лише `aiogram`/`httpx`/`dotenv` з PyPI), non-root,
  healthcheck — перевірка мережевої доступності `api.telegram.org`.
- `docker-compose.yml`: `app` перейменовано на `core-service`
  (відображає те, чим він тепер реально є — бізнес-ядро без Telegram);
  додано `ssh: [default]` у build core-service (раніше цього не було в
  compose, тільки `--mount=type=ssh` у Dockerfile — без forwarding'у з
  ssh-agent build просто не мав звідки взяти ключ); RPC-порт 8200 не
  прокинутий назовні за замовчуванням (той самий принцип, що й порт
  8100 у account-service). Новий блок `telegram-service` залежить від
  `core-service: condition: service_healthy` і не монтує жодних
  volume — увесь стан (`data/`, `logs/`) лишається виключно в
  core-service.
- `.dockerignore` (корінь репо) розширено: `telegram_service/`,
  `day_service/`, `tests/`, документація — не потрапляють у build
  context образу core-service (менший і швидший `docker build`).

Деталі протоколу й змінні середовища — `telegram_service/README.md`.


- Шифрування password у власній БД account-service (зараз plaintext, як
  було і раніше в `.env`/бізнес-БД — паритет, не регрес, але вартий уваги).
- Публікація `session_refreshed`/`is_banned` подій (див. вище).
- Автентифікація/мережева ізоляція account-service (зараз довіряємо
  внутрішній docker-мережі; порт 8100 відкритий назовні лише для дебагу —
  прибрати `ports:` в compose, якщо не потрібно).
