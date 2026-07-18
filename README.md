# reader (app)

Бізнес-застосунок (scheduler, professions: mining/quiz/daily/reader/...,
адмінський Telegram-бот). HTTP/auth/cookies/socket для акаунтів більше
не тут — це `account-service`, окремий репозиторій (сиблінг-директорія
`../account-service/service`, свій git/pyproject/Dockerfile).

Разом з ним (і опційно з `trade-helper` та `card-evaluation`) запускається
одним `docker-compose.yml` з кореня цього репо — див. коментарі там і
`ARCHITECTURE.md`. Очікувана структура директорій на хості:

```
some-folder/
├── app/                        ← цей репозиторій
├── account-service/            ← окремий репо
│   ├── service/                ← HTTP-сервіс (образ, який тут піднімається)
│   └── client/                 ← account-service-client, pip-пакет для інших сервісів
├── trade-helper/                ← окремий репо (опційно)
└── card-evaluation/            ← окремий репо (опційно, потрібен trade-helper)
    ├── service/                 ← бібліотека card_evaluation + HTTP-сервіс (образ)
    └── client/                  ← card-evaluation-client, pip-пакет для trade-helper
```

Запуск разом з account-service, card-evaluation, trade-helper та Redis — з кореня цього репо:

```bash
docker compose up --build
```

Runtime-конфіг (`app.yaml`, `.env`) монтується як volume, а не запікається
в образ — див. `docker-compose.yml`.
