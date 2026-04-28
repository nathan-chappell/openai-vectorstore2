# MCP OAuth Checklist

Use this checklist for HTTP MCP servers that protect user or tenant data.

## Normative Baseline

Primary standard: MCP Authorization specification for HTTP transports.

Current spec URL:

- https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization

Key referenced standards:

- OAuth 2.1
- RFC 8414: OAuth Authorization Server Metadata
- OpenID Connect Discovery 1.0, when used for AS discovery
- RFC 7591: Dynamic Client Registration, when used
- OAuth Client ID Metadata Documents, when used
- RFC 8707: Resource Indicators
- RFC 9728: OAuth Protected Resource Metadata
- RFC 6750-style bearer token resource requests and challenges

## Required Resource Server Behavior

- HTTP MCP auth is optional, but if used, the MCP server acts as an OAuth 2.1 resource server.
- The MCP server exposes protected resource metadata with `authorization_servers`.
- Metadata is discoverable by either:
  - `WWW-Authenticate: Bearer resource_metadata="..."` on `401`, preferably with `scope="..."`, or
  - well-known protected resource metadata at root or path-scoped URLs.
- Every HTTP request to MCP includes `Authorization: Bearer <access-token>` when protected.
- Access tokens are never accepted in query strings.
- The server validates token signature or introspection result, issuer, expiry/not-before, scopes, and audience/resource binding.
- The token must be issued specifically for the MCP server's canonical resource URI.
- The server returns:
  - `401` for missing, malformed, invalid, expired, or wrong-audience token.
  - `403` for valid token with insufficient scope.
  - `400` for malformed auth requests where applicable.

## Authorization Server Expectations

- Expose OAuth AS metadata or OIDC discovery metadata.
- Advertise `authorization_endpoint` and `token_endpoint`.
- Support authorization code + PKCE.
- Advertise `code_challenge_methods_supported` containing `S256`.
- Support one client registration approach accepted by the target client:
  - pre-registered client,
  - Client ID Metadata Documents,
  - Dynamic Client Registration.
- Preserve and enforce the `resource` parameter on authorization and token requests.
- Issue tokens with `aud`, `resource`, or an equivalent binding to the MCP resource.
- Validate exact redirect URIs.

## OpenAI ChatGPT Apps Compatibility

OpenAI ChatGPT Apps expect a public HTTPS MCP endpoint and will probe discovery paths. For authenticated apps, ChatGPT needs enough OAuth metadata to complete linking and then call `/mcp` with a bearer token.

OpenAI reference URL:

- https://developers.openai.com/apps-sdk/build/auth

Practical compatibility checks:

- `POST /mcp` without auth should not return SPA HTML. It should return `401` plus a useful bearer challenge.
- `/.well-known/oauth-protected-resource[/mcp]` should return JSON metadata, not the frontend index.
- The advertised authorization server metadata should include PKCE support.
- If relying on DCR, `registration_endpoint` must work for ChatGPT.
- The token endpoint must accept the `resource` parameter and bind the token to the MCP resource.
- Tool descriptors should accurately reflect read/write/destructive behavior.
- For tool-level OAuth UI, ChatGPT may also need `securitySchemes` and tool error metadata, but server-level linking still requires standards-compliant resource and AS discovery.

## Common Findings

- Token verifier only checks "is this an app session token" but not audience/resource binding.
- Resource metadata is missing because only a raw `TokenVerifier` is mounted, not a protected-resource metadata provider.
- Unknown `/.well-known/*` paths return frontend HTML with `200`.
- Authorization server metadata exists for browser auth but lacks `registration_endpoint` or Client ID Metadata Document support.
- OIDC metadata omits `code_challenge_methods_supported`, causing MCP clients to refuse PKCE.
- Server returns `401 invalid_token` without `WWW-Authenticate`, so clients do not know where to start OAuth.
- Required scopes are hardcoded in the accepted access token instead of validated from token claims/introspection.
- The same bearer token is passed through to upstream APIs, creating confused-deputy risk.

## Strong Auth-Test Definition

A deployed auth-test is meaningful when it runs against the same public URL, TLS, routing, auth provider, redirect URI policy, and token validation settings as production or staging.

Minimum passing test:

1. Unauthenticated MCP request returns `401` with valid `WWW-Authenticate`.
2. Protected resource metadata is valid JSON and advertises the correct resource and AS.
3. AS metadata is valid JSON and advertises PKCE `S256`.
4. A real authorization-code + PKCE flow obtains a token with `resource=https://host/mcp`.
5. A real MCP initialize/list-tools/read-only tool call succeeds with that token.
6. Wrong-audience, missing-scope, expired/revoked, and query-string tokens fail as expected.

Even after this passes, run one ChatGPT connector creation/refresh smoke test before release.
