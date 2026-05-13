from __future__ import annotations

from openai_vectorstore2_backend.app.bootstrap import create_services
from openai_vectorstore2_backend.app.core.config import get_settings
from openai_vectorstore2_backend.app.mcp.server import create_mcp_server


def main() -> None:
    settings = get_settings()
    services = create_services(settings)
    server = create_mcp_server(settings, services)
    server.run(transport="stdio")
