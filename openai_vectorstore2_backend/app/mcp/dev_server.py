from __future__ import annotations

from fastmcp import FastMCP

from openai_vectorstore2_backend.app.bootstrap import AppServices, create_services
from openai_vectorstore2_backend.app.core.config import AppSettings, get_settings
from openai_vectorstore2_backend.app.mcp.server import create_dev_mcp_server

settings: AppSettings = get_settings()
services: AppServices = create_services(settings)
mcp: FastMCP = create_dev_mcp_server(settings, services)
