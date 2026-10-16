# One container: builds the website, then runs the API + background checker that serves it.
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --silent
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    JOB_WATCHER_DB=/data/jobwatcher.db \
    JOB_WATCHER_FRONTEND=/app/frontend/dist \
    JOB_WATCHER_BROWSER_CHANNEL=
WORKDIR /app/backend
COPY backend/requirements.txt ./
# Playwright's own Chromium works on both x86 and ARM servers (Google Chrome is x86-only on Linux).
RUN pip install --no-cache-dir -r requirements.txt playwright==1.49.1 \
 && playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*
COPY backend/app ./app
COPY --from=web /web/dist /app/frontend/dist
VOLUME /data
EXPOSE 8090
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/api/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
