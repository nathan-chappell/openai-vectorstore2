---
name: mcp-oauth-auditor
description: Audit, implement, or test HTTP MCP server authentication against the MCP Authorization specification, OAuth 2.1, RFC 9728 protected-resource metadata, RFC 8707 resource indicators, PKCE, DCR or client metadata documents, and OpenAI ChatGPT Apps connector expectations. Use when debugging ChatGPT MCP OAuth errors, reviewing bearer-token verifiers, designing Clerk/Auth0/Stytch-backed MCP auth, or validating a deployed /mcp URL.
---

# MCP OAuth Auditor

Use this skill to review or test an HTTP MCP server's auth flow as a resource server. Prefer the current MCP Authorization specification over vendor docs, then check OpenAI ChatGPT Apps behavior as an interoperability target.

## Core Workflow

1. Identify the target mode.
   - For temporary ChatGPT smoke tests, no-auth may be acceptable if the deployment is isolated and disposable.
   - For production or shared data, require OAuth resource-server auth.
   - STDIO MCP is outside this HTTP OAuth flow; it should retrieve credentials from the local environment.

2. Read the local auth implementation.
   - Inspect MCP server creation, auth provider/verifier wiring, route mounts, `WWW-Authenticate` handling, and any SPA/static catch-all.
   - Search with:

```bash
rg -n "mcp|OAuth|oauth|openid|well-known|WWW-Authenticate|TokenVerifier|RemoteAuthProvider|authorization|resource" backend docs tests
```

3. Use the standards checklist.
   - Load `references/mcp-oauth-checklist.md` when doing a review, planning implementation, or interpreting probe output.
   - Treat missing audience/resource validation, fake well-known `200` HTML, and accepting upstream app/session tokens directly as high-risk findings.

4. Probe a deployed URL when available.
   - The URL should be the public HTTPS MCP endpoint, usually `https://host.example/mcp`.
   - Run the bundled script for discovery and negative checks:

```bash
python skills/mcp-oauth-auditor/scripts/probe_mcp_oauth.py https://host.example/mcp
```

5. Run a real token path when possible.
   - If you already have a test access token:

```bash
python skills/mcp-oauth-auditor/scripts/probe_mcp_oauth.py https://host.example/mcp --access-token "$ACCESS_TOKEN"
```

   - If the authorization server supports Dynamic Client Registration, or you provide a pre-registered client, run the PKCE browser flow:

```bash
python skills/mcp-oauth-auditor/scripts/probe_mcp_oauth.py https://host.example/mcp --run-oauth
python skills/mcp-oauth-auditor/scripts/probe_mcp_oauth.py https://host.example/mcp --run-oauth --client-id "$CLIENT_ID"
```

   - The script prints an authorization URL and listens on a localhost redirect URI by default. Visit the URL, complete login/consent, and let the redirect return to the script. For Clerk-backed flows, use a test OAuth app/client that allows the printed redirect URI, or enable DCR in the auth proxy.

6. Judge readiness.
   - A passing deployed auth-test must cover both discovery and a real MCP request with a resource-bound token.
   - Still do one ChatGPT developer-mode connector smoke test before release, because ChatGPT-specific client registration and UI behavior can differ from a generic OAuth test client.

## Expected Review Output

Lead with findings ordered by severity. Include file/line references for local code and exact deployed URLs/statuses for live probes. For each issue, state the violated standard expectation and the likely ChatGPT symptom.

Recommended sections:

- Findings
- Standards Gaps
- OpenAI/ChatGPT Compatibility Notes
- Auth-Test Plan
- Implementation Next Steps

## Implementation Guidance

- Prefer an OAuth authorization server or auth proxy in front of Clerk rather than accepting Clerk session tokens directly at the MCP resource server.
- Use `RemoteAuthProvider`-style protected resource metadata when the MCP server verifies tokens issued by a separate authorization server.
- Ensure the authorization server supports authorization code + PKCE with `S256`.
- Ensure tokens are audience-bound to the canonical MCP resource URI, commonly `https://host.example/mcp`.
- Do not let the frontend SPA catch-all return `200` for unknown `/.well-known/*` routes.
- Do not put access tokens in query strings; reject them.
- Return `401` for missing/invalid/expired tokens and `403` for valid tokens with insufficient scope.

## Resources

- `references/mcp-oauth-checklist.md`: standards checklist, OpenAI compatibility notes, and review heuristics.
- `scripts/probe_mcp_oauth.py`: deployed URL probe and optional OAuth PKCE flow tester.
