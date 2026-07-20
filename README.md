# core-service (колишній "app")

Бізнес-ядро (scheduler, professions: mining/quiz/daily/reader/...).
HTTP/auth/cookies/socket для акаунтів тут немає — це `account-service`,
окремий репозиторій (сиблінг-директорія `../account-service/service`,
свій git/pyproject/Dockerfile). Адмінського Telegram-бота теж немає —
він виїхав у власний контейнер `telegram_service/` (сиблінг-директорія
в корені ЦЬОГО репо, не окремий git-репозиторій), що звертається сюди
виключно по HTTP RPC (`src/core/rpc/server.py`, деталі —
`telegram_service/README.md` і `ARCHITECTURE.md`).

Разом з ним запускається одним `docker-compose.yml` з кореня цього репо —
`account-service`, `card-evaluation` і обидва сервіси `trade-helper`
(`api`/`telegram`) підняті тут же, в одній docker-мережі, і всі звертаються
до ОДНОГО й того самого `account-service` та ОДНОГО й того самого
`card-evaluation` — жоден із сервісів не створює свій окремий інстанс.
Див. коментарі в `docker-compose.yml` і `ARCHITECTURE.md`. Очікувана
структура директорій на хості:

```
some-folder/
├── app/                        ← цей репозиторій (core-service + main.py + src/)
│   └── telegram_service/       ← сиблінг-директорія В ЦЬОМУ репо (не окремий git) —
│                                  окремий образ/pyproject/Dockerfile, Telegram UI-шар
├── account-service/            ← окремий репо
│   ├── service/                ← HTTP-сервіс (образ, який тут піднімається)
│   └── client/                 ← account-service-client, pip-пакет для інших сервісів
├── trade-helper/                ← окремий репо; api/telegram піднімаються
│                                  звідси, з ../trade-helper/docker-compose.yml
│                                  тут НЕ використовується (він лише для
│                                  окремого dev-запуску без core-service)
└── card-evaluation/            ← окремий репо, потрібен і core-service, і trade-helper
    ├── service/                 ← бібліотека card_evaluation + HTTP-сервіс (образ)
    └── client/                  ← card-evaluation-client, pip-пакет для trade-helper
```

Запуск разом з account-service, card-evaluation, trade-helper та Redis — з кореня цього репо:

```bash
docker compose up --build
```

Runtime-конфіг (`app.yaml`, `.env`) монтується як volume, а не запікається
в образ — див. `docker-compose.yml`.

## Приватні репозиторії (account-service-client / card-evaluation-client)

`card-evaluation` і `trade-helper` встановлюють свої клієнтські пакети з
приватних git-репозиторіїв по SSH (`git+ssh://git@github.com/...`). Перед
`docker compose up --build` переконайся, що:

1. У тебе запущений `ssh-agent` з доданим ключем, який має доступ до цих
   репозиторіїв (`ssh-add -l` показує ключ; `ssh -T git@github.com`
   підключається успішно).
2. BuildKit увімкнено (в сучасних версіях Docker — за замовчуванням;
   інакше `export DOCKER_BUILDKIT=1 COMPOSE_DOCKER_CLI_BUILD=1`).

Сам форвардинг ключа в білд уже прописаний у `docker-compose.yml`
(`ssh: [default]` в кожному відповідному сервісі) — додатково нічого
передавати в команду не треба.
