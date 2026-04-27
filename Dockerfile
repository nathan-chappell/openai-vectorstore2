FROM node:25-slim AS frontend-build
WORKDIR /app

ARG PUBLIC_CLERK_PUBLISHABLE=
ARG PUBLIC_API_BASE=/api
ARG PUBLIC_CHATKIT_DOMAIN=domain_pk_build_placeholder

COPY package.json package-lock.json ./
COPY frontend ./frontend
COPY vendor ./vendor
RUN npm ci
RUN VITE_CLERK_PUBLISHABLE_KEY=${PUBLIC_CLERK_PUBLISHABLE} \
    VITE_API_BASE_URL=${PUBLIC_API_BASE} \
    VITE_CHATKIT_DOMAIN_KEY=${PUBLIC_CHATKIT_DOMAIN} \
    npm run build

FROM python:3.14-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY vendor ./vendor
COPY backend ./backend
COPY alembic.ini ./
COPY migrations ./migrations
RUN pip install --no-cache-dir .

COPY --from=frontend-build /app/frontend/dist ./frontend/dist

EXPOSE 8000
CMD ["openai-vectorstore2-http"]
