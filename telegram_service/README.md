# telegram-service

Адмінський Telegram-бот, винесений з монолуту `core-service` в окремий
образ/процес. Раніше жив як daemon-thread усередині бізнес-застосунку
(`AdminBotRunner`, той самий event loop, ті самі `SchedulerService`-виклики
"в пам'яті"); тепер — окремий контейнер без жодного знання про
scheduler/professions/DB, що спілкується з `core-service` виключно по
HTTP RPC.

## Протокол

```
telegram_service.core_client.CoreServiceClient
        │  POST {CORE_SERVICE_URL}/rpc/{method}
        │  body: {"args": [...], "kwargs": {...}}
        ▼
core-service:  src/core/rpc/server.py  →  SchedulerService.<method>(...)
```

Той самий generic-RPC стиль, що вже використаний для `account-service`
(`POST /accounts/{id}/request`) — маленький explicit whitelist методів
замість десятків окремих REST-ручок.

`CoreServiceClient` навмисно повторює сигнатури методів
`SchedulerService` 1:1 — тому всі роутери (`routers/accounts/*`,
`routers/stats.py`, `routers/logs.py`, `routers/help.py`) виглядають так,
ніби й досі викликають локальний фасад; різниця лише в тому, що виклик
іде по мережі.

## Чому НЕ спільний volume для логів

`/logs` у боті раніше читав файли з диска напряму (`LogReader`, спільна
файлова система в одному контейнері). Тепер `core-service` віддає ті самі
дані через RPC (`logs_tail_account`, `logs_tail_scheduler`, `logs_errors`,
`logs_list_accounts`) — контейнери діляться мережею, а не файлами.

## Змінні середовища

| Змінна                | Призначення                                            |
|------------------------|---------------------------------------------------------|
| `ADMIN_BOT_TOKEN`      | токен Telegram-бота                                    |
| `ADMIN_IDS`            | id адмінів через кому                                  |
| `CORE_SERVICE_URL`     | напр. `http://core-service:8200`                       |
| `CORE_SERVICE_TOKEN`   | той самий токен, що й у `core-service` (опційно, dev)  |

## Запуск

Піднімається разом з `core-service` і `redis` з кореневого
`docker-compose.yml` — див. `../ARCHITECTURE.md`.

```bash
docker compose up --build telegram-service
```

Локально (без Docker):

```bash
cd telegram_service
pip install -e .
export ADMIN_BOT_TOKEN=... ADMIN_IDS=... CORE_SERVICE_URL=http://localhost:8200
python -m telegram_service.main
```
