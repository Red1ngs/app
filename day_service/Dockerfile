FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY day_service ./day_service

# Єдина залежність — клієнт Redis (asyncio). Без web-фреймворку: сервіс не
# слухає жодного порту, тому EXPOSE/uvicorn тут навмисно немає.
RUN pip install --no-cache-dir . --break-system-packages

VOLUME ["/data"]

# Без HTTP — healthcheck дивиться на "пульс" (mtime файла, який
# command-loop торкається після кожної успішної спроби зв'язку з Redis),
# а не на HTTP /health. Не додає жодних залежностей (тільки stdlib).
HEALTHCHECK --interval=10s --timeout=5s --retries=10 \
    CMD python3 -c "import os,sys,time; p=os.getenv('DAY_SERVICE_HEARTBEAT_PATH','/tmp/day_service_heartbeat'); sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p)<30 else 1)"

CMD ["python3", "-m", "day_service.main"]
