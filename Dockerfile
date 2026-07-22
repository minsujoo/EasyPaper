# ── Stage 1: 프론트엔드 빌드 ──
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/index.html frontend/vite.config.js ./
COPY frontend/src ./src
RUN npm run build

# ── Stage 2: 백엔드 런타임 ──
FROM python:3.12-slim
WORKDIR /app/backend

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# config.py는 .env/translation_prompt.txt를 항상 backend/(이 이미지에서는
# /app/backend) 기준 상대경로로 읽고 쓴다 - 심볼릭 링크로 /data 볼륨 안을
# 가리키게 해서, 설정 화면에서 바꾼 계정 정보나 번역 프롬프트 커스터마이징이
# 컨테이너를 재생성해도 사라지지 않고 남도록 한다.
RUN ln -sf /data/.env .env && ln -sf /data/translation_prompt.txt translation_prompt.txt

# 문서 DB/업로드/캐시/라이브러리를 컨테이너 밖에 영속화하기 위한 데이터 볼륨.
# services/db.py의 DB_PATH, config.py의 UPLOAD_DIR/CACHE_DIR/LIBRARY_DIR는
# 모두 이 환경변수를 우선 사용하도록 되어 있다 (미설정 시 네이티브 실행과
# 동일하게 backend/ 하위 상대경로로 폴백).
ENV DB_PATH=/data/easypaper.db \
    UPLOAD_DIR=/data/uploads \
    CACHE_DIR=/data/cache \
    LIBRARY_DIR=/data/library \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/', timeout=3)" || exit 1

CMD ["python", "main.py"]
