FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY main.py ./

RUN pip install --no-cache-dir -e . --break-system-packages

VOLUME ["/app/data", "/app/logs"]

# app.yaml і .env монтуються docker-compose'ом (це runtime-конфіг, не образ)
CMD ["python", "main.py"]
