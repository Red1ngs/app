# telegram-service — самостійний образ. Жодних приватних git-залежностей,
# жодних build-tools: усі пакети (aiogram/httpx/dotenv) — звичайний PyPI,
# тому один-єдиний легкий шар без git/ssh/build-essential (на відміну
# від core-service, якому вони потрібні лише для account-service-client).
FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY telegram_service ./telegram_service

RUN pip install --no-cache-dir . --break-system-packages \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
    CMD python3 -c "import socket; socket.create_connection(('api.telegram.org', 443), timeout=4)" || exit 1

CMD ["python3", "-m", "telegram_service.main"]
