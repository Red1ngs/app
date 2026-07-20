# core-service — бізнес-ядро (scheduler, professions). Telegram тут більше
# немає (див. ../telegram_service) — образ трохи менший і простіший, ніж
# був у монолуті, але єдина причина лишити git/ssh взагалі — приватна
# залежність account-service-client. Тому: build-стадія з git/ssh/
# build-essential + venv, і тонкий runtime-шар БЕЗ них.

# ── build ─────────────────────────────────────────────────────────────────
FROM python:3.13-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p -m 0700 ~/.ssh && ssh-keyscan github.com >> ~/.ssh/known_hosts

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
COPY main.py ./

# --mount=type=ssh прокидає ключ з хоста лише на час цього RUN — приватний
# репозиторій account-service тягнеться, ключ у фінальний шар НЕ потрапляє.
# Навмисно НЕ editable (`-e`): фінальна стадія копіює лише /opt/venv, без
# /build, тож editable-install (посилання на вихідники поза venv) там
# просто не працював би. Звичайний `pip install .` кладе `src` як
# самодостатній пакет всередину venv.
RUN --mount=type=ssh pip install --no-cache-dir .

# ── runtime ───────────────────────────────────────────────────────────────
FROM python:3.13-slim

RUN useradd --create-home --uid 10001 appuser
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /app
COPY --chown=appuser:appuser main.py ./
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN mkdir -p /app/data /app/logs && chown -R appuser:appuser /app/data /app/logs
VOLUME ["/app/data", "/app/logs"]

# USER appuser  ← прибрати звідси: контейнер стартує root'ом,
# entrypoint сам виконує setpriv і більше root ніколи не працює.
EXPOSE 8200

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "main.py"]
