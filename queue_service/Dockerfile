FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY queue_service ./queue_service

RUN pip install --no-cache-dir -e . --break-system-packages

VOLUME ["/data"]

CMD ["python", "-m", "queue_service.main"]
