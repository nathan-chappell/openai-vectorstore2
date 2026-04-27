# Deployment

The beta deployment path is Docker-first. Build and publish images from a checkout with the private submodule initialized:

```bash
git submodule update --init --recursive
docker build \
  --build-arg PUBLIC_CLERK_PUBLISHABLE="$VITE_CLERK_PUBLISHABLE_KEY" \
  --build-arg PUBLIC_API_BASE=/api \
  --build-arg PUBLIC_CHATKIT_DOMAIN="$VITE_CHATKIT_DOMAIN_KEY" \
  -t nathanschappell/openai-vectorstore2:1.0.0 .
docker push nathanschappell/openai-vectorstore2:1.0.0
```

## Railway

Use the published Docker image or let Railway build the Dockerfile.

- Health check: `/health`
- Internal port: `8000`
- Start command with migrations: `sh -lc 'alembic upgrade head && openai-vectorstore2-http'`
- Plain start command after a confirmed migration: `openai-vectorstore2-http`

Run Alembic before serving each deploy when `DATABASE_SCHEMA_MODE=migrations`. The app can also verify an empty database during startup, but migrations are the production path because `create_all` does not alter existing tables.

## Required Env

Set these for a real deployment:

```bash
OPENAI_API_KEY=
APP_SIGNING_SECRET=
APP_BASE_URL=https://your-service.example
CORS_ORIGINS=https://your-service.example
DATABASE_URL=postgresql://...
DATABASE_SCHEMA_MODE=migrations
VITE_CHATKIT_DOMAIN_KEY=
```

`openai_responses` is still the default agent provider. The first compatibility-mode configuration surface is available for OpenAI-compatible `/v1/chat/completions` endpoints:

```bash
AGENT_MODEL_PROVIDER=openai_responses
CHAT_COMPLETIONS_MODEL=gpt-5.4-mini
CHAT_COMPLETIONS_BASE_URL=
CHAT_COMPLETIONS_API_KEY=
CHAT_COMPLETIONS_CONTEXT_WINDOW_TOKENS=
CHAT_COMPLETIONS_WEB_SEARCH_URL=
CHAT_COMPLETIONS_ON_PREM_PRICE_PER_MILLION_TOKENS=1.0
```

Leave `CHAT_COMPLETIONS_BASE_URL` and `CHAT_COMPLETIONS_API_KEY` empty to use the normal OpenAI client defaults and `OPENAI_API_KEY`. Set `CHAT_COMPLETIONS_CONTEXT_WINDOW_TOKENS` for private or OSS models whose context size is not in the app's known model table. On-prem billing uses the placeholder per-million-token rate until real infrastructure costs are modeled.

Set Clerk values when browser auth is enabled:

```bash
CLERK_SECRET_KEY=
CLERK_ISSUER_URL=
VITE_CLERK_PUBLISHABLE_KEY=
CLERK_AUTHORIZED_PARTIES=
```

## Storage

Local container storage is acceptable only for a throwaway demo. For a stable beta link, use either a Railway volume mounted at `LOCAL_STORAGE_DIR` or S3-compatible storage:

```bash
STORAGE_BACKEND=s3
S3_ENDPOINT=
S3_BUCKET=
S3_ACCESS_KEY_ID=
S3_SECRET_ACCESS_KEY=
S3_REGION=auto
S3_URL_STYLE=path
```

Stored source files, generated assets, and report artifacts all use this storage boundary. Losing local ephemeral storage leaves database rows pointing at missing payloads, so avoid ephemeral local storage for anything you intend to keep.

## Admin, Credits, And Payments

The default public provider keeps checkout disabled and supports local/manual admin credit operations. The private shared submodule can be enabled with:

```bash
ADMIN_INTEGRATION_PROVIDER=ai_portfolio_admin
ADMIN_SHARED_MODULE=backend.app.admin.shared_adapter
```

Billing defaults are intentionally light:

```bash
BILLING_ENABLED=true
BILLING_DEFAULT_CREDIT_FLOOR_USD=-1.0
BILLING_PLATFORM_MARKUP_MULTIPLIER=1.3
BILLING_UNKNOWN_MODEL_POLICY=zero
```

Manual credit grants and cost events live in this app's database, including events created from ChatKit, REST, and MCP operations.

## Logs

Railway should collect stdout/stderr. To avoid writing container-local log files in production, set:

```bash
LOG_FILE_PATH=
LOG_LEVEL=INFO
```

Logs include task/source IDs and OpenAI response/conversation IDs, but should not include prompts, secrets, or bulky content.
