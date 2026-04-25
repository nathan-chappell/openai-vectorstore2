from __future__ import annotations

from backend.app.bootstrap import create_services
from backend.app.core.config import get_settings
from backend.app.mcp.server import create_mcp_server


def main() -> None:
    settings = get_settings()
    services = create_services(settings)
    server = create_mcp_server(settings, services)
    server.run(transport="stdio")
