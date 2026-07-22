"""
core/rpc — generic HTTP RPC поверх `SchedulerService`.

Той самий стиль, що й `account-service`/`day-service`: маленький, явний
whitelist методів, generic dispatch-endpoint замість 20+ окремих
REST-ручок, bearer-токен для внутрішньо-мережевої автентифікації.

Єдиний споживач — `telegram-service` (сиблінг-директорія `../telegram_service`),
через `telegram_service.core_client.CoreServiceClient`.
"""
