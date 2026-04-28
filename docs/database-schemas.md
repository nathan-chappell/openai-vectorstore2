# Database Schemas

This project can share a physical PostgreSQL service with PlodAI, but the apps
must not share one unqualified schema. They have independent Alembic histories,
and today some same-named billing tables have different column contracts.

## Target Layout

Use `public` for portfolio-wide account, admin, billing, payment, and usage
tracking state:

- user identity/account mirrors
- credit balances
- credit grants
- free-credit requests
- payment attempts
- cost and usage events

Use one app schema per product for app-owned state:

- `openai_vectorstore2`: libraries, source files, filesystem entries, semantic
  chunks, stored assets, tasks, report artifacts, ChatKit threads, entries, and
  attachments.
- `plodai`: farms, farm records, farm images, farm chats, farm chat entries, and
  farm chat attachments.

## Alembic Ownership

Each ownership boundary needs its own version table:

- shared/public migrations: `public.shared_alembic_version`
- this app's migrations: `openai_vectorstore2.alembic_version`
- PlodAI migrations: `plodai.alembic_version`

Do not let multiple apps write different revision IDs into the same
`public.alembic_version` row. That is the startup failure mode when both apps
point at one PostgreSQL database and one default schema.

## Current Deployable Workaround

Until the shared/public stream exists, isolate this app in its own schema:

```bash
DATABASE_POSTGRES_SCHEMA=openai_vectorstore2
```

The app creates that schema before running migrations and uses
`openai_vectorstore2,public` as the connection search path. App-owned tables
therefore land in `openai_vectorstore2`, while public shared tables remain
visible to adapters that need them. This keeps the current app bootable next to
PlodAI while the shared table contract is consolidated.

## Migration Path

1. Define the shared table contract once, preferably in `ai-portfolio-admin`.
2. Move shared admin/billing/payment migrations into a shared/public stream.
3. Keep this repo's Alembic stream responsible only for `openai_vectorstore2`
   app tables.
4. Reinitialize PlodAI against the same physical database with a `plodai` app
   schema and the shared public tables.
5. Update both app adapters so grants, payments, and cost debits go through the
   shared models while app-specific source/thread/task IDs are stored as
   metadata or nullable context columns on the shared usage events.
