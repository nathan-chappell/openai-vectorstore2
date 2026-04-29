# Auth

The app supports two web auth modes and one MCP verifier path.

## Local Dev

Local dev auth is disabled by default. Enable it only for local development or
test runs with `ALLOW_LOCAL_DEV_AUTH=true`.

- The frontend uses local mode when `VITE_CLERK_PUBLISHABLE_KEY` is empty.
- The bearer token is `local-dev`.
- The backend maps that token to a synthetic active user with `clerk_user_id="local-dev"`.
- The synthetic user is an admin. Never enable this in production.

This is the mode used by Playwright. Browser tests explicitly clear Clerk env vars and set local-dev auth.

## Clerk Web

When `VITE_CLERK_PUBLISHABLE_KEY` is set, the frontend wraps the app with `ClerkProvider`.

- `frontend/src/main.tsx` obtains Clerk tokens with `useAuth().getToken()`.
- REST and ChatKit requests send the token as `Authorization: Bearer ...`.
- `backend/app/services/auth.py` validates Clerk session or OAuth tokens and then loads user profile metadata from Clerk.
- Account activation and admin role checks are driven by Clerk public metadata. The default keys are `active`, `role`, and `credit_floor_usd`.

Relevant env vars:

```bash
ALLOW_LOCAL_DEV_AUTH=false
CLERK_SECRET_KEY=
VITE_CLERK_PUBLISHABLE_KEY=
CLERK_AUTHORIZED_PARTIES=
CLERK_ACTIVE_METADATA_KEY=active
CLERK_ROLE_METADATA_KEY=role
```

For production, `ALLOW_LOCAL_DEV_AUTH=false`, `CLERK_SECRET_KEY`, and
`VITE_CLERK_PUBLISHABLE_KEY` should all be set deliberately.
`CLERK_AUTHORIZED_PARTIES` should be set to the deployed frontend origin when
Clerk audience/authorized-party checks are available for the token type in use.

## Admin Boundary

The default public implementation uses the in-repo auth and billing services. A private shared admin/auth/payments package can be enabled later with `ADMIN_INTEGRATION_PROVIDER=ai_portfolio_admin`; see [Admin Integration](admin-integration.md).

## MCP HTTP

FastMCP uses `VectorstoreTokenVerifier`.

- The verifier delegates bearer-token validation to the same app `AuthService` used by REST.
- Required MCP resource scopes default to `profile,email` through
  `MCP_REQUIRED_SCOPES`, matching Clerk's MCP helper examples.
- `MCP_AUTHORIZATION_SERVERS` points at the upstream OAuth provider, currently
  Clerk for production. Protected-resource metadata advertises that upstream
  issuer directly, as expected by the OpenAI Apps SDK auth guidance and Clerk's
  MCP tooling. The app also exposes old-compat authorization-server metadata
  routes that proxy Clerk's discovery document without rewriting the issuer or
  registration endpoint.
- Clerk authorization-server metadata advertises OIDC scopes such as `openid`
  and refresh-token support through `offline_access`. ChatGPT should request
  advertised OIDC scopes during OAuth, and the dynamically registered Clerk
  OAuth client must have those scopes enabled.
- Local-dev MCP calls can still resolve to `local-dev` only when local-dev auth
  is explicitly enabled or auth middleware is skipped in tests/local tooling.
- `MCP_AUTH_MODE=none` disables the HTTP MCP verifier for temporary ChatGPT
  developer-mode smoke tests and maps calls to `local-dev`. Do not use this for
  production or shared data.
- OAuth metadata endpoints expose public CORS headers for browser-based MCP
  clients. If ChatGPT calls `/mcp/` from the browser, production
  `CORS_ORIGINS` must also include `https://chatgpt.com` and
  `https://chat.openai.com`.

For production ChatGPT Apps, recreate the app/connector after metadata or Clerk
OAuth settings change so ChatGPT refetches discovery metadata and creates a
fresh OAuth client. If ChatGPT creates a Clerk OAuth app without `openid`, delete
that stale dynamic client and reconnect after refreshing/recreating the ChatGPT
app.

## MCP Stdio

The stdio entry point is intended for local hosts and uses the same app services. Keep production user isolation on the HTTP MCP path unless a stdio host has a clear local-user boundary.
