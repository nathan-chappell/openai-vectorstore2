from __future__ import annotations

from dataclasses import dataclass

from agents import set_default_openai_key

from backend.app.admin import build_auth_service
from backend.app.chatkit.server import VectorstoreChatKitServer
from backend.app.chatkit.store import VectorstoreChatStore
from backend.app.core.config import AppSettings
from backend.app.core.logging import configure_logging
from backend.app.db.session import DatabaseManager
from backend.app.integrations.openai_gateway import OpenAIGateway
from backend.app.services import ActionService, AuthService, BillingService, ResearchImportService, SourceService
from backend.app.storage import StorageService, build_storage_service


@dataclass(slots=True)
class AppServices:
    settings: AppSettings
    database: DatabaseManager
    auth: AuthService
    billing: BillingService
    storage: StorageService
    openai: OpenAIGateway
    sources: SourceService
    research: ResearchImportService
    actions: ActionService
    chat_store: VectorstoreChatStore
    chatkit_server: VectorstoreChatKitServer
    _closed: bool = False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.chatkit_server.close()
        await self.sources.close()
        await self.openai.close()
        await self.auth.close()
        await self.database.close()


def create_services(settings: AppSettings) -> AppServices:
    configure_logging(
        settings.log_level,
        file_path=settings.normalized_log_file_path,
        file_max_bytes=settings.log_file_max_bytes,
        file_backup_count=settings.log_file_backup_count,
    )
    set_default_openai_key(settings.openai_api_key.get_secret_value())
    database = DatabaseManager(settings)
    auth = build_auth_service(settings)
    billing = BillingService(settings=settings, database=database)
    storage = build_storage_service(settings)
    openai = OpenAIGateway(settings)
    sources = SourceService(
        settings=settings,
        database=database,
        auth=auth,
        storage=storage,
        openai=openai,
    )
    actions = ActionService(
        settings=settings,
        database=database,
        sources=sources,
        storage=storage,
        openai=openai,
        billing=billing,
    )
    research = ResearchImportService(
        settings=settings,
        database=database,
        sources=sources,
        openai=openai,
    )
    chat_store = VectorstoreChatStore(database=database, sources=sources)
    chatkit_server = VectorstoreChatKitServer(
        settings=settings,
        store=chat_store,
        sources=sources,
        research=research,
        actions=actions,
        openai=openai,
        billing=billing,
    )
    return AppServices(
        settings=settings,
        database=database,
        auth=auth,
        billing=billing,
        storage=storage,
        openai=openai,
        sources=sources,
        research=research,
        actions=actions,
        chat_store=chat_store,
        chatkit_server=chatkit_server,
    )
