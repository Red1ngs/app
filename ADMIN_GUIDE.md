# Гайд адміністратора — docker-compose стек (app + account-service + redis)

## 0. Перед першим запуском

```bash
cp .env.example .env         # заповнити ADMIN_BOT_TOKEN, ADMIN_IDS, ACCOUNT_PASSWORD_*
# покласти app.yaml поруч з docker-compose.yml (той самий, що й раніше)
docker compose up --build -d
docker compose ps            # усі 3 сервіси мають бути healthy
```

---

## 1. Керування контейнерами

```bash
# Запуск / зупинка всього стека
docker compose up -d
docker compose down                 # зупинити, лишити volumes (дані цілі)
docker compose down -v              # ⚠ зупинити + ВИДАЛИТИ volumes (втрата БД/redis)

# Перезапуск одного сервісу (напр. після зміни app.yaml — без ребілду образу)
docker compose restart app
docker compose restart account-service

# Ребілд після зміни коду
docker compose up -d --build app
docker compose up -d --build account-service

docker compose up -d  # перезапуск усього

# Статус / health
docker compose ps

# Логи (stdout контейнера — той самий формат, що й раніше в консолі)
docker compose logs -f app
docker compose logs -f account-service
docker compose logs -f redis
docker compose logs -f --tail 200 app account-service   # обидва разом

# Зайти всередину контейнера
docker compose exec app bash
docker compose exec account-service bash
```

---

## 2. Керування акаунтами через Telegram admin-бота

Без змін відносно того, що вже було — акаунт-сервіс під капотом, бот той самий:

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

> Додавання акаунта через бота записує пароль/проксі в `.env` бізнес-застосунку
> (`ACCOUNT_PASSWORD_<slug>`, `ACCOUNT_PROXY_<slug>`) — так само, як і раніше.
> Сам логін (звернення на сайт) тепер виконує account-service — бот лише
> просить його через порт.

---

## 3. Прямі HTTP-команди до account-service (дебаг / скрипти)

За замовчуванням порт 8100 прокинутий назовні (`docker-compose.yml → account-service.ports`).
Якщо стек на тій самій машині:

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

### Ручна реєстрація акаунта (зазвичай робить сам бізнес-застосунок при /accounts add)
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

### Ручний generic-запит через сесію акаунта (те, що зазвичай робить бізнес-застосунок)
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

# Скільки підписників зараз слухає (має бути >=1 — це app/account_event_bus)
docker compose exec redis redis-cli pubsub numsub 'account_events:acc1'
```

---

## 5. Бекап / відновлення даних

Дані лежать у трьох named volumes:

| Volume | Вміст |
|---|---|
| `account-data` | `accounts.db` — облікові дані + сесії (account-service) |
| `app-data` | `bot_state.db` — бізнес-дані (акаунти/mangas/inventory) |
| `app-logs` | логи бізнес-застосунку |

```bash
# Бекап (приклад для account-data)
docker run --rm -v <project>_account-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/account-data-$(date +%F).tar.gz -C /data .

# Відновлення
docker run --rm -v <project>_account-data:/data -v "$PWD":/backup alpine \
  sh -c "cd /data && tar xzf /backup/account-data-YYYY-MM-DD.tar.gz"
```

`<project>` — префікс, який docker compose додає до імен volume (зазвичай
назва теки репозиторію). Уточнити: `docker volume ls`.

---

## 6. Типові проблеми

| Симптом | Куди дивитись |
|---|---|
| Акаунт не підключається | `docker compose logs -f account-service` — там весь login/auth лог |
| Бот не бачить нові socket-події (сповіщення/трейди) | `docker compose logs -f app \| grep AccountEventBus` — переконатись, що слухач стартував; перевірити `redis-cli pubsub numsub` (п.4) |
| `account-service недоступний` в логах app | `docker compose ps` — чи healthy account-service; `curl $BASE/health` |
| Втрачена сесія / 401 після рестарту account-service | Нормально — сесія тримається в пам'яті процесу; `sm`/scheduler у app перепідключить при наступному циклі, або `POST /accounts/{id}/connect` вручну |
| Треба скинути все і почати з чистого аркуша | `docker compose down -v` (⚠ видаляє БД обох сервісів і redis) |
