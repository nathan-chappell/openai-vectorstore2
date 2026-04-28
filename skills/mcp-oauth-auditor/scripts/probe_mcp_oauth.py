#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import secrets
import threading
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import httpx


JSON = dict[str, Any]


@dataclass
class ProbeResult:
    server_url: str
    checks: list[JSON] = field(default_factory=list)
    resource_metadata: JSON | None = None
    authorization_server_metadata: JSON | None = None
    token_response: JSON | None = None
    access_token_mcp_status: int | None = None

    def add(self, name: str, status: str, **details: object) -> None:
        self.checks.append({"name": name, "status": status, **details})


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    server: "OAuthCallbackServer"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        self.server.callback_query = {key: values[0] for key, values in query.items() if values}
        body = b"OAuth callback received. You can return to the terminal.\n"
        self.send_response(200)
        self.send_header("content-type", "text/plain")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return None


class OAuthCallbackServer(HTTPServer):
    callback_query: dict[str, str] | None = None


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe a deployed HTTP MCP OAuth flow.")
    parser.add_argument("server_url", help="Public HTTPS MCP endpoint, usually https://host.example/mcp")
    parser.add_argument("--access-token", help="Existing access token to test against the MCP endpoint.")
    parser.add_argument("--expect-no-auth", action="store_true", help="Treat an unprotected MCP endpoint as an expected smoke-test mode.")
    parser.add_argument("--run-oauth", action="store_true", help="Run authorization-code + PKCE in a browser.")
    parser.add_argument("--client-id", help="Pre-registered OAuth client id. If omitted, DCR is attempted.")
    parser.add_argument("--client-secret", help="Optional OAuth client secret for token exchange.")
    parser.add_argument("--redirect-uri", default="http://127.0.0.1:8765/callback")
    parser.add_argument("--protocol-version", default="2025-11-25", help="MCP protocol version for initialize.")
    parser.add_argument("--scope", help="Override requested OAuth scope.")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--listen-timeout", type=float, default=180.0)
    args = parser.parse_args()

    server_url = normalize_url(args.server_url)
    result = ProbeResult(server_url=server_url)
    with httpx.Client(timeout=args.timeout, follow_redirects=False) as client:
        unauthenticated = probe_unauthenticated_mcp(
            client,
            server_url,
            args.protocol_version,
            args.expect_no_auth,
            result,
        )
        if args.expect_no_auth and unauthenticated.status_code < 400:
            print(json.dumps(dataclass_to_json(result), indent=2, sort_keys=True))
            return
        metadata_url = resource_metadata_url_from_challenge(unauthenticated)
        metadata = discover_resource_metadata(client, server_url, metadata_url, result)
        result.resource_metadata = metadata
        auth_metadata = discover_authorization_server_metadata(client, metadata, result) if metadata else None
        result.authorization_server_metadata = auth_metadata

        token = args.access_token
        if args.run_oauth:
            if metadata is None or auth_metadata is None:
                result.add("oauth-flow", "fail", reason="missing resource or authorization server metadata")
            else:
                token_response = run_oauth_flow(
                    client=client,
                    auth_metadata=auth_metadata,
                    resource_metadata=metadata,
                    server_url=server_url,
                    redirect_uri=args.redirect_uri,
                    scope=args.scope,
                    client_id=args.client_id,
                    client_secret=args.client_secret,
                    listen_timeout=args.listen_timeout,
                    result=result,
                )
                result.token_response = redact_token_response(token_response)
                token = string_value(token_response, "access_token")

        if token:
            result.access_token_mcp_status = probe_authenticated_mcp(client, server_url, token, args.protocol_version, result)

    print(json.dumps(dataclass_to_json(result), indent=2, sort_keys=True))


def probe_unauthenticated_mcp(
    client: httpx.Client,
    server_url: str,
    protocol_version: str,
    expect_no_auth: bool,
    result: ProbeResult,
) -> httpx.Response:
    response = post_mcp_initialize(client, server_url, token=None, protocol_version=protocol_version, follow_redirects=False)
    if response.is_redirect:
        result.add("mcp-unauthenticated-redirect", "warn", status_code=response.status_code, location=response.headers.get("location"))
        redirected_url = absolute_location(server_url, response.headers.get("location"))
        if redirected_url:
            response = post_mcp_initialize(
                client,
                redirected_url,
                token=None,
                protocol_version=protocol_version,
                follow_redirects=False,
            )

    challenge = response.headers.get("www-authenticate")
    content_type = response.headers.get("content-type", "")
    if response.status_code == 401 and challenge:
        result.add("mcp-unauthenticated", "pass", status_code=response.status_code, www_authenticate=challenge)
    elif response.status_code == 401:
        result.add("mcp-unauthenticated", "fail", status_code=response.status_code, reason="missing WWW-Authenticate header")
    elif response.status_code < 400:
        result.add(
            "mcp-unauthenticated",
            "pass" if expect_no_auth else "fail",
            status_code=response.status_code,
            reason="MCP endpoint appears unprotected",
            content_type=content_type,
        )
    else:
        result.add("mcp-unauthenticated", "warn", status_code=response.status_code, content_type=content_type, body=response.text[:300])
    return response


def post_mcp_initialize(
    client: httpx.Client,
    server_url: str,
    *,
    token: str | None,
    protocol_version: str,
    follow_redirects: bool,
) -> httpx.Response:
    headers = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": protocol_version,
            "capabilities": {},
            "clientInfo": {"name": "mcp-oauth-auditor", "version": "0.1.0"},
        },
    }
    return client.post(server_url, json=payload, headers=headers, follow_redirects=follow_redirects)


def discover_resource_metadata(
    client: httpx.Client,
    server_url: str,
    preferred_url: str | None,
    result: ProbeResult,
) -> JSON | None:
    candidates = []
    if preferred_url:
        candidates.append(preferred_url)
    candidates.extend(resource_metadata_candidates(server_url))

    seen: set[str] = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        response = client.get(url)
        parsed_json = response_json(response)
        if response.status_code == 200 and parsed_json is not None:
            if "authorization_servers" in parsed_json:
                result.add("resource-metadata", "pass", url=url)
                return parsed_json
            result.add("resource-metadata-shape", "fail", url=url, reason="missing authorization_servers", body=parsed_json)
            return None
        if response.status_code == 200:
            result.add("resource-metadata", "fail", url=url, reason="200 response was not JSON", content_type=response.headers.get("content-type"), body=response.text[:120])
            return None
        result.add("resource-metadata-candidate", "warn", url=url, status_code=response.status_code)

    result.add("resource-metadata", "fail", reason="no protected resource metadata found")
    return None


def discover_authorization_server_metadata(
    client: httpx.Client,
    resource_metadata: Mapping[str, Any],
    result: ProbeResult,
) -> JSON | None:
    authorization_servers = resource_metadata.get("authorization_servers")
    if not isinstance(authorization_servers, list) or not authorization_servers:
        result.add("authorization-servers", "fail", reason="metadata has no authorization_servers")
        return None

    for issuer in authorization_servers:
        if not isinstance(issuer, str):
            continue
        for url in authorization_metadata_candidates(issuer):
            response = client.get(url)
            parsed_json = response_json(response)
            if response.status_code == 200 and parsed_json is not None:
                result.add("authorization-server-metadata", "pass", issuer=issuer, url=url)
                validate_authorization_metadata(parsed_json, result)
                return parsed_json
            if response.status_code == 200:
                result.add("authorization-server-metadata", "fail", issuer=issuer, url=url, reason="200 response was not JSON", content_type=response.headers.get("content-type"))
                return None
            result.add("authorization-server-metadata-candidate", "warn", issuer=issuer, url=url, status_code=response.status_code)

    result.add("authorization-server-metadata", "fail", reason="no AS metadata found")
    return None


def validate_authorization_metadata(metadata: Mapping[str, Any], result: ProbeResult) -> None:
    for key in ["authorization_endpoint", "token_endpoint"]:
        if isinstance(metadata.get(key), str):
            result.add(f"as-{key}", "pass", value=metadata[key])
        else:
            result.add(f"as-{key}", "fail", reason="missing or non-string")

    methods = metadata.get("code_challenge_methods_supported")
    if isinstance(methods, list) and "S256" in methods:
        result.add("as-pkce-s256", "pass")
    else:
        result.add("as-pkce-s256", "fail", reason="code_challenge_methods_supported must include S256")

    if isinstance(metadata.get("registration_endpoint"), str):
        result.add("as-registration", "pass", mode="dynamic_client_registration", value=metadata["registration_endpoint"])
    elif metadata.get("client_id_metadata_document_supported") is True:
        result.add("as-registration", "pass", mode="client_id_metadata_document")
    else:
        result.add("as-registration", "warn", reason="no registration_endpoint or client_id_metadata_document_supported advertised")


def run_oauth_flow(
    *,
    client: httpx.Client,
    auth_metadata: Mapping[str, Any],
    resource_metadata: Mapping[str, Any],
    server_url: str,
    redirect_uri: str,
    scope: str | None,
    client_id: str | None,
    client_secret: str | None,
    listen_timeout: float,
    result: ProbeResult,
) -> JSON:
    authorization_endpoint = required_url(auth_metadata, "authorization_endpoint")
    token_endpoint = required_url(auth_metadata, "token_endpoint")
    if client_id is None:
        client_id = register_dynamic_client(client, auth_metadata, redirect_uri, scope, result)

    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    requested_scope = scope or scope_from_resource_metadata(resource_metadata)
    auth_params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": canonical_resource(resource_metadata, server_url),
    }
    if requested_scope:
        auth_params["scope"] = requested_scope
    auth_url = f"{authorization_endpoint}?{urlencode(auth_params)}"
    result.add("oauth-authorization-url", "manual", url=auth_url, redirect_uri=redirect_uri)
    print("\nOpen this URL in a browser, complete login/consent, then return here:\n")
    print(auth_url)
    print()

    callback_query = wait_for_oauth_callback(redirect_uri, listen_timeout)
    if callback_query.get("state") != state:
        raise RuntimeError("OAuth callback state did not match.")
    if "error" in callback_query:
        raise RuntimeError(f"OAuth callback error: {callback_query}")
    code = callback_query.get("code")
    if not code:
        raise RuntimeError("OAuth callback did not include a code.")

    token_payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
        "resource": canonical_resource(resource_metadata, server_url),
    }
    if client_secret:
        token_payload["client_secret"] = client_secret
    response = client.post(token_endpoint, data=token_payload, headers={"accept": "application/json"})
    token_json = response_json(response)
    if response.status_code >= 400 or token_json is None:
        result.add("oauth-token-exchange", "fail", status_code=response.status_code, body=response.text[:500])
        response.raise_for_status()
        raise RuntimeError("Token endpoint did not return JSON.")
    if isinstance(token_json.get("access_token"), str):
        result.add("oauth-token-exchange", "pass", token_type=token_json.get("token_type"), scope=token_json.get("scope"))
    else:
        result.add("oauth-token-exchange", "fail", reason="missing access_token", body=redact_token_response(token_json))
    return token_json


def register_dynamic_client(
    client: httpx.Client,
    auth_metadata: Mapping[str, Any],
    redirect_uri: str,
    scope: str | None,
    result: ProbeResult,
) -> str:
    registration_endpoint = auth_metadata.get("registration_endpoint")
    if not isinstance(registration_endpoint, str):
        raise RuntimeError("No --client-id provided and AS metadata has no registration_endpoint.")
    payload: JSON = {
        "client_name": "MCP OAuth Auditor",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    if scope:
        payload["scope"] = scope
    response = client.post(registration_endpoint, json=payload, headers={"accept": "application/json"})
    parsed_json = response_json(response)
    if response.status_code >= 400 or parsed_json is None:
        result.add("dynamic-client-registration", "fail", status_code=response.status_code, body=response.text[:500])
        response.raise_for_status()
        raise RuntimeError("DCR did not return JSON.")
    client_id = string_value(parsed_json, "client_id")
    if not client_id:
        result.add("dynamic-client-registration", "fail", reason="missing client_id", body=parsed_json)
        raise RuntimeError("DCR response missing client_id.")
    result.add("dynamic-client-registration", "pass", client_id=client_id)
    return client_id


def wait_for_oauth_callback(redirect_uri: str, timeout: float) -> dict[str, str]:
    parsed = urlparse(redirect_uri)
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("This script can only listen on localhost redirect URIs.")
    port = parsed.port
    if port is None:
        raise RuntimeError("Redirect URI must include a port.")
    server = OAuthCallbackServer((parsed.hostname, port), OAuthCallbackHandler)
    server.timeout = 0.5

    def serve() -> None:
        while server.callback_query is None:
            server.handle_request()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    thread.join(timeout)
    server.server_close()
    if server.callback_query is None:
        raise TimeoutError(f"Timed out waiting for OAuth callback at {redirect_uri}.")
    return server.callback_query


def probe_authenticated_mcp(
    client: httpx.Client,
    server_url: str,
    token: str,
    protocol_version: str,
    result: ProbeResult,
) -> int:
    response = post_mcp_initialize(
        client,
        server_url,
        token=token,
        protocol_version=protocol_version,
        follow_redirects=True,
    )
    if response.status_code < 400:
        result.add("mcp-authenticated-initialize", "pass", status_code=response.status_code, content_type=response.headers.get("content-type"))
    elif response.status_code == 403:
        result.add("mcp-authenticated-initialize", "fail", status_code=403, reason="token valid but insufficient scope or policy denied", body=response.text[:500])
    else:
        result.add("mcp-authenticated-initialize", "fail", status_code=response.status_code, body=response.text[:500])
    return response.status_code


def resource_metadata_candidates(server_url: str) -> list[str]:
    parsed = urlparse(server_url)
    origin = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    path = parsed.path.strip("/")
    candidates = []
    if path:
        candidates.append(f"{origin}/.well-known/oauth-protected-resource/{path}")
    candidates.append(f"{origin}/.well-known/oauth-protected-resource")
    return candidates


def authorization_metadata_candidates(issuer: str) -> list[str]:
    parsed = urlparse(issuer)
    origin = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    path = parsed.path.strip("/")
    if path:
        return [
            f"{origin}/.well-known/oauth-authorization-server/{path}",
            f"{origin}/.well-known/openid-configuration/{path}",
            f"{origin}/{path}/.well-known/openid-configuration",
        ]
    return [
        f"{origin}/.well-known/oauth-authorization-server",
        f"{origin}/.well-known/openid-configuration",
    ]


def resource_metadata_url_from_challenge(response: httpx.Response) -> str | None:
    challenge = response.headers.get("www-authenticate")
    if not challenge:
        return None
    marker = 'resource_metadata="'
    start = challenge.find(marker)
    if start == -1:
        return None
    start += len(marker)
    end = challenge.find('"', start)
    if end == -1:
        return None
    return challenge[start:end]


def scope_from_resource_metadata(metadata: Mapping[str, Any]) -> str:
    scopes = metadata.get("scopes_supported")
    if isinstance(scopes, list):
        return " ".join(scope for scope in scopes if isinstance(scope, str))
    return ""


def canonical_resource(metadata: Mapping[str, Any], server_url: str) -> str:
    resource = metadata.get("resource")
    if isinstance(resource, str) and resource:
        return resource.rstrip("/")
    return server_url.rstrip("/")


def required_url(metadata: Mapping[str, Any], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Authorization server metadata missing {key}.")
    return value


def response_json(response: httpx.Response) -> JSON | None:
    try:
        value = response.json()
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def string_value(mapping: Mapping[str, Any], key: str) -> str | None:
    value = mapping.get(key)
    return value if isinstance(value, str) and value else None


def redact_token_response(value: Mapping[str, Any] | None) -> JSON | None:
    if value is None:
        return None
    redacted = dict(value)
    for key in ["access_token", "refresh_token", "id_token"]:
        if key in redacted:
            redacted[key] = "[redacted]"
    return redacted


def dataclass_to_json(result: ProbeResult) -> JSON:
    return {
        "server_url": result.server_url,
        "checks": result.checks,
        "resource_metadata": result.resource_metadata,
        "authorization_server_metadata": result.authorization_server_metadata,
        "token_response": result.token_response,
        "access_token_mcp_status": result.access_token_mcp_status,
    }


def normalize_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    if not parsed.scheme or not parsed.netloc:
        raise SystemExit("server_url must be an absolute URL, e.g. https://host.example/mcp")
    return raw_url.rstrip("/")


def absolute_location(base_url: str, location: str | None) -> str | None:
    if not location:
        return None
    return urljoin(base_url, location)


if __name__ == "__main__":
    main()
