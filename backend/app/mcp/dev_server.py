from __future__ import annotations

from fastmcp import FastMCP

from backend.app.bootstrap import AppServices, create_services
from backend.app.core.config import AppSettings, get_settings
from backend.app.mcp.server import create_dev_mcp_server

settings: AppSettings = get_settings()
services: AppServices = create_services(settings)
mcp: FastMCP = create_dev_mcp_server(settings, services)
