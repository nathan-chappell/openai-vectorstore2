# Security Posture

This app should deploy with production-safe defaults. Local shortcuts must be
explicit opt-ins.

## Must Set For Production

These values are required or effectively required for a deployed instance:

```bash
OPENAI_API_KEY=
APP_SIGNING_SECRET=
APP_BASE_URL=https://your-service.example
CORS_ORIGINS=https://your-service.example
DATABASE_URL=postgresql://...
DATABASE_SCHEMA_MODE=migrations
DATABASE_POSTGRES_SCHEMA=openai_vectorstore2
ALLOW_LOCAL_DEV_AUTH=false
CLERK_SECRET_KEY=
CLERK_ISSUER_URL=
VITE_CLERK_PUBLISHABLE_KEY=
VITE_CHATKIT_DOMAIN_KEY=
```

For persistent user data, also set a durable storage backend:

```bash
STORAGE_BACKEND=s3
S3_ENDPOINT=
S3_BUCKET=
S3_ACCESS_KEY_ID=
S3_SECRET_ACCESS_KEY=
S3_REGION=auto
S3_URL_STYLE=path
```

For payment receipt credit, set:

```bash
PAYPAL_RECIPIENT_EMAIL=
```

## Auth Defaults

`ALLOW_LOCAL_DEV_AUTH` defaults to `false`. When true, the backend accepts
`Authorization: Bearer local-dev` and maps it to an active admin user. Settings
also reject `ALLOW_LOCAL_DEV_AUTH=true` unless `APP_BASE_URL` is localhost,
`127.0.0.1`, or `::1`.

Local development and Playwright can still opt in explicitly:

```bash
ALLOW_LOCAL_DEV_AUTH=true
APP_BASE_URL=http://localhost:8000
```

## Deployment Notes

- Use Clerk in production. Without Clerk values and with local-dev auth disabled,
  protected REST, ChatKit, and MCP calls should fail closed with 401s.
- Set `CORS_ORIGINS` to the exact deployed frontend origin. Do not deploy with
  broad origins.
- Prefer S3-compatible storage or a persistent volume. Container-local storage
  loses uploaded sources and generated artifacts on restart.
- Set `LOG_FILE_PATH=` on platforms that collect stdout/stderr to avoid writing
  logs to ephemeral container storage.
- Keep `DATABASE_SCHEMA_MODE=migrations`; `create_all` is for throwaway empty
  databases only.
- Set `CLERK_AUTHORIZED_PARTIES` when your Clerk token flow provides a stable
  frontend origin/audience to enforce.
