# Auth

The app supports two web auth modes and one MCP verifier path.

## Local Dev

Local dev auth is enabled when `ALLOW_LOCAL_DEV_AUTH=true`.

- The frontend uses local mode when `VITE_CLERK_PUBLISHABLE_KEY` is empty.
- The bearer token is `local-dev`.
- The backend maps that token to a synthetic active user with `clerk_user_id="local-dev"`.

This is the mode used by Playwright. Browser tests explicitly clear Clerk env vars and set local-dev auth.

## Clerk Web

When `VITE_CLERK_PUBLISHABLE_KEY` is set, the frontend wraps the app with `ClerkProvider`.

- `frontend/src/main.tsx` obtains Clerk tokens with `useAuth().getToken()`.
- REST and ChatKit requests send the token as `Authorization: Bearer ...`.
- `backend/app/services/auth.py` validates Clerk session or OAuth tokens and then loads user profile metadata from Clerk.
- Account activation is currently driven by Clerk private metadata. The default keys are `active` and `role`.

Relevant env vars:

```bash
CLERK_SECRET_KEY=
CLERK_ISSUER_URL=
VITE_CLERK_PUBLISHABLE_KEY=
CLERK_AUTHORIZED_PARTIES=
CLERK_ACTIVE_METADATA_KEY=active
CLERK_ROLE_METADATA_KEY=role
```

## MCP HTTP

FastMCP uses `VectorstoreTokenVerifier`.

- The verifier delegates bearer-token validation to the same app `AuthService` used by REST.
- Required scopes default to `openid,email,profile` through `MCP_REQUIRED_SCOPES`.
- Local-dev MCP calls can still resolve to `local-dev` when auth middleware is skipped in tests or local tooling.

For production ChatGPT Apps, the remaining work is OAuth/provider metadata hardening: protected-resource metadata, authorization-server metadata, audience/resource checks, and final HTTPS `APP_BASE_URL` configuration.

## MCP Stdio

The stdio entry point is intended for local hosts and uses the same app services. Keep production user isolation on the HTTP MCP path unless a stdio host has a clear local-user boundary.
