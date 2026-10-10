FROM node:24-bookworm-slim AS web
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ENV DEVFLOW_API_URL=http://127.0.0.1:8000 NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 NODE_ENV=production
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates libstdc++6 && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.lock.txt ./backend/requirements.lock.txt
RUN pip install --no-cache-dir -r backend/requirements.lock.txt
COPY --from=web /usr/local/bin/node /usr/local/bin/node
RUN node --version
COPY --from=web --chown=10001:10001 /app/frontend/ ./frontend/
COPY backend/app/ ./backend/app/
COPY backend/skills/ ./backend/skills/
COPY infra/cloud/run_service.py ./infra/cloud/run_service.py
RUN useradd --create-home --uid 10001 devflow && mkdir -p /app/data && chown devflow:devflow /app/data
USER devflow
ENV PORT=8080 DEVFLOW_DATA_DIR=/app/data
EXPOSE 8080
CMD ["python", "infra/cloud/run_service.py"]
