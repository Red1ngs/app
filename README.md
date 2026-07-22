# mangabuff-bot — монорепо

Три незалежні сервіси, які піднімаються разом одним `docker-compose.yml`
з кореня цього репо:

```
.
├── docker-compose.yml     ← redis + account-service + day-service +
│                              core-service + telegram-service, одна мережа
├── ARCHITECTURE.md        ← детальна історія рефакторингів і поточна архітектура
├── ADMIN_GUIDE.md         ← гайд для оператора (адмінський Telegram-бот)
│
├── core-service/          ← бізнес-ядро: scheduler, professions
│                              (mining/quiz/daily/reader/...). Див.
│                              core-service/README.md
├── day_service/           ← "настав новий день" для акаунта. Див.
│                              day_service/README.md
└── telegram_service/      ← адмінський Telegram UI, ходить у core-service
                               по HTTP RPC. Див. telegram_service/README.md
```

Кожен сервіс — самодостатня директорія зі своїм `Dockerfile` і
`pyproject.toml`; корінь репо в жодному Docker build участі не бере,
там лишається тільки оркестрація (`docker-compose.yml`) і спільна
документація.

`account-service` — окремий репозиторій-сиблінг (`../account-service`,
поза цим репо): власний образ, власна БД сесій, спільний і для
core-service, і за потреби для інших зовнішніх сервісів.

## Запуск

```bash
docker compose up --build
```

Перед першим запуском:

- скопіювати `.env.example` → `.env` і заповнити токени
  (`ADMIN_BOT_TOKEN`, `ADMIN_IDS`, за потреби `ACCOUNT_SERVICE_TOKEN`,
  `CORE_SERVICE_TOKEN`);
- переконатись, що `ssh-agent` має ключ з доступом до приватного репо
  `account-service-client` (потрібен лише для build `core-service`,
  деталі — `core-service/README.md`).

## Де що шукати

- Як влаштований конкретний сервіс — README у його директорії.
- Історія рефакторингів, обґрунтування рішень, детальна поточна
  архітектура — `ARCHITECTURE.md`.
- Як користуватись адмінським Telegram-ботом — `ADMIN_GUIDE.md`.
