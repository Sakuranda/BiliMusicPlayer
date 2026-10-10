# ---- 第一阶段：构建前端 ----
FROM node:24-alpine AS web
WORKDIR /app
COPY web/package.json web/pnpm-lock.yaml ./
RUN corepack enable && corepack prepare pnpm@11.21.0 --activate && pnpm install --frozen-lockfile
COPY web .
RUN pnpm build

# ---- 第二阶段：Python 后端（托管前端静态文件）----
FROM python:3.12-slim

# ffmpeg：yt-dlp 抽取/转码音频所需
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/app ./app
COPY server/scripts ./scripts
COPY --from=web /app/dist /app/webdist

ENV DATA_DIR=/data \
    WEB_DIST=/app/webdist
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
