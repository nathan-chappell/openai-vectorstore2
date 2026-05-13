# Deployment

The beta deployment path is Docker-first. Build and publish images from a checkout with the private submodule initialized:

```bash
git submodule update --init --recursive
docker build \
  --build-arg PUBLIC_CLERK_PUBLISHABLE="$VITE_CLERK_PUBLISHABLE_KEY" \
  --build-arg PUBLIC_API_BASE=/api \
  --build-arg PUBLIC_CHATKIT_DOMAIN="$VITE_CHATKIT_DOMAIN_KEY" \
  -t nathanschappell/openai-vectorstore2:1.1.1 .
docker push nathanschappell/openai-vectorstore2:1.1.1
```

## Railway

Use the published Docker image or let Railway build the Dockerfile.

- Health check: `/health`
- Internal port: use Railway's injected `PORT`; local/default Docker fallback is `8000`.
- Start command with migrations: `sh -lc 'alembic upgrade head && openai-vectorstore2-http'`
- Plain start command after a confirmed migration: `openai-vectorstore2-http`

Run Alembic before serving each deploy when `DATABASE_SCHEMA_MODE=migrations`. The app can also verify an empty database during startup, but migrations are the production path because `create_all` does not alter existing tables.
Railway's public domain target port must match the port the app listens on. Do
not set a conflicting `HOST`; the app binds to `0.0.0.0`.

## Required Env

Set these for a real deployment:

```bash
OPENAI_API_KEY=
APP_BASE_URL=https://your-service.example
CORS_ORIGINS=https://your-service.example
DATABASE_URL=postgresql://...
DATABASE_SCHEMA_MODE=migrations
DATABASE_POSTGRES_SCHEMA=openai_vectorstore2
ALLOW_LOCAL_DEV_AUTH=false
CLERK_SECRET_KEY=
VITE_CLERK_PUBLISHABLE_KEY=
VITE_CHATKIT_DOMAIN_KEY=
```

`ALLOW_LOCAL_DEV_AUTH` defaults to `false` and should stay false in production.
When true, the backend accepts `Authorization: Bearer local-dev` as an active
admin user.

See `docs/security.md` for the production security checklist.

Use a separate database for each portfolio app when possible. If PlodAI and
OpenAI Vectorstore2 must share the same PostgreSQL database service today, keep
them in separate schemas because their Alembic histories and same-named
billing tables are not yet a shared contract. For example, leave PlodAI on
`public` for the current instance and set:

```bash
DATABASE_POSTGRES_SCHEMA=openai_vectorstore2
```

The app will create the schema if it is missing and run Alembic with a
`openai_vectorstore2,public` search path. Do not point both apps at the same
PostgreSQL schema.

The intended shared-service layout is `public` for shared account, credit,
payment, and usage tracking tables, plus one app schema per product. See
`docs/database-schemas.md` before consolidating PlodAI and this app onto one
database.

`openai_responses` is still the default agent provider. The first compatibility-mode configuration surface is available for OpenAI-compatible `/v1/chat/completions` endpoints:

```bash
AGENT_MODEL_PROVIDER=openai_responses
CHAT_COMPLETIONS_MODEL=gpt-5.4-mini
CHAT_COMPLETIONS_BASE_URL=
CHAT_COMPLETIONS_API_KEY=
CHAT_COMPLETIONS_CONTEXT_WINDOW_TOKENS=
CHAT_COMPLETIONS_WEB_SEARCH_URL=
```

Leave `CHAT_COMPLETIONS_BASE_URL` and `CHAT_COMPLETIONS_API_KEY` empty to use the normal OpenAI client defaults and `OPENAI_API_KEY`. Set `CHAT_COMPLETIONS_CONTEXT_WINDOW_TOKENS` for private or OSS models whose context size is not in the app's known model table.

For the simple PayPal receipt flow, set a personal or business PayPal recipient email. `PAYPAL_PAYMENT_URL` is optional; when present the account panel links users to it.

```bash
PAYPAL_RECIPIENT_EMAIL=you@example.com
PAYPAL_PAYMENT_URL=
PAYPAL_MIN_PAYMENT_USD=5.0
PAYPAL_MAX_PAYMENT_USD=250.0
```

This flow does not use PayPal checkout or webhooks. Users create a reference code, pay externally, upload text/PDF/email-style proof, and receive immediate receipt-backed credits when the receipt matches. Those credits remain available unless an admin rejects/revokes the attempt, which records a reversal adjustment.

Set Clerk values when browser auth is enabled:

```bash
CLERK_SECRET_KEY=
VITE_CLERK_PUBLISHABLE_KEY=
CLERK_AUTHORIZED_PARTIES=
```

For deployed browser auth, treat `CLERK_SECRET_KEY`, `VITE_CLERK_PUBLISHABLE_KEY`,
and a deployment-specific `CORS_ORIGINS` value as required. Set
`CLERK_AUTHORIZED_PARTIES` when your Clerk token flow supplies a stable frontend
origin/audience to check.

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
ADMIN_SHARED_MODULE=openai_vectorstore2_backend.app.admin.shared_adapter
```

Billing defaults are intentionally light:

```bash
BILLING_ENABLED=true
BILLING_DEFAULT_CREDIT_FLOOR_USD=-1.0
BILLING_PLATFORM_MARKUP_MULTIPLIER=1.3
BILLING_UNKNOWN_MODEL_POLICY=zero
BILLING_SEMANTIC_SPLIT_COST_USD=0.002
BILLING_RESEARCH_DISCOVERY_COST_USD=0.01
BILLING_VECTOR_SEARCH_COST_USD=0.0005
BILLING_VECTOR_INDEX_FILE_COST_USD=0.002
BILLING_IMAGE_GENERATION_COST_USD=0.04
BILLING_VOICE_GENERATION_COST_PER_1K_CHARS_USD=0.02
```

Manual grants, free-credit request grants, receipt-backed PayPal grants, reversal adjustments, and cost events live in this app's database. Some non-ChatKit OpenAI operations use the configurable placeholder rates above until provider usage/pricing data is richer.

## Logs

Railway should collect stdout/stderr. To avoid writing container-local log files in production, set:

```bash
LOG_FILE_PATH=
LOG_LEVEL=INFO
```

Logs include task/source IDs and OpenAI response/conversation IDs, but should not include prompts, secrets, or bulky content.
