FROM node:25-slim AS frontend-build
WORKDIR /app

COPY package.json package-lock.json ./
COPY frontend ./frontend
COPY vendor ./vendor
RUN npm ci
RUN npm run build

FROM python:3.14-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY vendor ./vendor
COPY backend ./backend
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY migrations ./migrations
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

EXPOSE 8000
CMD ["openai-vectorstore2-http"]
