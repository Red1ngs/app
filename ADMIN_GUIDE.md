# Гайд адміністратора — docker-compose стек

Сервіси: `redis`, `account-service`, `day-service`, `core-service`,
`telegram-service`. Детальніше про кожен — README у відповідній
директорії (`core-service/`, `day_service/`, `telegram_service/`) і
`ARCHITECTURE.md`.

## 0. Перед першим запуском

```bash
cp .env.example .env         # заповнити ADMIN_BOT_TOKEN, ADMIN_IDS
# core-service/app.yaml — вже в репо, редагувати на місці за потреби
docker compose up --build -d
docker compose ps            # усі 5 сервісів мають бути healthy
```

---

## 1. Керування контейнерами

```bash
# Запуск / зупинка всього стека
docker compose up -d
docker compose down                 # зупинити, дані на диску (../data/*) цілі
docker compose down -v              # ⚠ те саме + видалити named volumes (тут їх нема — синонім down)

# Перезапуск одного сервісу (напр. після зміни app.yaml — без ребілду образу)
docker compose restart core-service
docker compose restart account-service
docker compose restart day-service
docker compose restart telegram-service

# Ребілд після зміни коду
docker compose up -d --build core-service
docker compose up -d --build account-service
docker compose up -d --build day-service
docker compose up -d --build telegram-service

docker compose up -d  # перезапуск усього

# Статус / health
docker compose ps

# Логи (stdout контейнера)
docker compose logs -f core-service
docker compose logs -f account-service
docker compose logs -f day-service
docker compose logs -f telegram-service
docker compose logs -f redis
docker compose logs -f --tail 200 core-service account-service   # кілька разом

# Зайти всередину контейнера
docker compose exec core-service bash
docker compose exec account-service bash
```

---

## 2. Керування акаунтами через Telegram admin-бота

| Команда | Дія |
|---|---|
| `/accounts` | список акаунтів (кнопки, кольоровий статус) |
| 🔎 Пошук | за ID / email / професією / статусом / підключенням / проксі |
| 🗂 Категорії | групування списку |
| `/stats` | загальна статистика |
| `/stats <id>` | статистика конкретного акаунта |
| `/logs` | меню логів |
| `/logs errors` | помилки за 24 год |
| `/logs scheduler` | лог scheduler'а |
| `/logs account <id>` | лог конкретного акаунта |

З меню акаунта (кнопки): ⏸ Призупинити / ▶️ Відновити, 🎓 Професії, 🛠 Налаштування професії, 🗑 Видалити, ➕ Додати новий акаунт.

> Бот (`telegram-service`) не має прямого доступу до БД/логів — усі ці
> команди йдуть по HTTP RPC у `core-service` (`POST /rpc/{method}`,
> див. `telegram_service/README.md`). Додавання акаунта передає
> пароль/проксі напряму на `account-service` (`add_account` викликає
> `account_client.register(...)` один раз) — `core-service` їх ніде НЕ
> зберігає (ні в `.env`, ні в БД), лише `account_id`+email для власного
> обліку. Сам логін (звернення на сайт) виконує `account-service` — і
> `core-service`, і бот лише просять його через мережу.

---

## 3. Прямі HTTP-команди до account-service (дебаг / скрипти)

Порт 8100 за замовчуванням НЕ прокинутий назовні (закоментовано в
`docker-compose.yml`, з тих же міркувань безпеки — `/accounts/{id}/request`
дає повний доступ до сесій акаунтів). Для дебагу розкоментуй `ports:` у
сервісі `account-service` і обов'язково задай `ACCOUNT_SERVICE_TOKEN`.
Після цього:

```bash
BASE=http://localhost:8100
```

### Здоров'я сервісу
```bash
curl -s $BASE/health
```

### Список акаунтів + статус (idle / connecting / connected / error / dead)
```bash
curl -s $BASE/accounts | jq
```

### Статус одного акаунта
```bash
curl -s $BASE/accounts/<account_id> | jq
```

### Ручна реєстрація акаунта (зазвичай робить сам core-service при /accounts add)
```bash
curl -s -X POST $BASE/accounts \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"acc1","email":"foo@bar.com","password":"secret","proxy":null}' | jq
```

### Підключити / відключити сесію
```bash
curl -s -X POST $BASE/accounts/acc1/connect | jq
curl -s -X POST $BASE/accounts/acc1/disconnect
```

### Скинути (invalidate) сесію — примусовий re-login при наступному запиті
```bash
curl -s -X POST $BASE/accounts/acc1/session/invalidate
```

### Ручний generic-запит через сесію акаунта (те, що зазвичай робить core-service)
```bash
curl -s -X POST $BASE/accounts/acc1/request \
  -H 'Content-Type: application/json' \
  -d '{"method":"GET","url":"/mine","room":"/mine","priority":"NORMAL"}' | jq
```

⚠ Ці ручні команди діють в обхід бізнес-логіки (scheduler, professions) —
корисно для дебагу, але не для нормальної роботи (акаунти, підключені так,
scheduler не бачить і не веде облік).

---

## 4. Redis — перевірка та live-моніторинг подій

```bash
docker compose exec redis redis-cli ping                 # PONG

# Живий потік усіх socket-подій усіх акаунтів (як їх бачить account-service)
docker compose exec redis redis-cli --csv psubscribe 'account_events:*'

# Тільки конкретний акаунт
docker compose exec redis redis-cli subscribe 'account_events:acc1'

# Скільки підписників зараз слухає (має бути >=1 — це core-service/account_event_bus)
docker compose exec redis redis-cli pubsub numsub 'account_events:acc1'

# Черга команд day-service (реєстрація/зняття акаунтів з розкладу "новий день")
docker compose exec redis redis-cli llen day_service:commands
```

---

## 5. Бекап / відновлення даних

Дані — bind mounts у `../data/` (на рівень вище цього репо на хості), НЕ
named docker volumes:

| Шлях на хості | Вміст |
|---|---|
| `../data/redis` | дамп redis (за потреби persistence) |
| `../data/account` | `accounts.db` — облікові дані + сесії (account-service) |
| `../data/day` | `day_service.db` — журнал спрацювань day-service |
| `../data/core` | `bot_state.db` — бізнес-дані (акаунти/mangas/inventory) core-service |
| `../data/logs` | логи core-service |

```bash
# Бекап (приклад для account-service)
tar czf account-data-$(date +%F).tar.gz -C ../data/account .

# Відновлення
tar xzf account-data-YYYY-MM-DD.tar.gz -C ../data/account
```

Зупиняти відповідний сервіс на час бекапу/відновлення необов'язково для
читання (SQLite витримує паралельне читання), але рекомендовано для
відновлення, щоб уникнути запису поверх відкритої БД.

---

## 6. Типові проблеми

| Симптом | Куди дивитись |
|---|---|
| Акаунт не підключається | `docker compose logs -f account-service` — там весь login/auth лог |
| Бот не бачить нові socket-події | `docker compose logs -f core-service \| grep AccountEventBus` — переконатись, що слухач стартував; перевірити `redis-cli pubsub numsub` (п.4) |
| `account-service недоступний` в логах core-service | `docker compose ps` — чи healthy account-service; за потреби розкоментувати порт і `curl $BASE/health` (п.3) |
| Втрачена сесія / 401 після рестарту account-service | Нормально — сесія тримається в пам'яті процесу; scheduler у core-service перепідключить при наступному циклі, або `POST /accounts/{id}/connect` вручну (п.3) |
| Новий день не оголошується | `docker compose logs -f day-service`; перевірити heartbeat-файл усередині контейнера (healthcheck) |
| Треба скинути все і почати з чистого аркуша | `docker compose down` + видалити відповідні підтеки в `../data/` вручну (⚠ незворотно) |
