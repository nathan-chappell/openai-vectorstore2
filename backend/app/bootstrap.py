from __future__ import annotations

from dataclasses import dataclass

from backend.app.chatkit.server import VectorstoreChatKitServer
from backend.app.chatkit.store import VectorstoreChatStore
from backend.app.core.config import AppSettings
from backend.app.core.logging import configure_logging
from backend.app.db.session import DatabaseManager
from backend.app.integrations.openai_gateway import OpenAIGateway
from backend.app.services import ActionService, AuthService, SourceService
from backend.app.storage import StorageService, build_storage_service


@dataclass(slots=True)
class AppServices:
    settings: AppSettings
    database: DatabaseManager
    auth: AuthService
    storage: StorageService
    openai: OpenAIGateway
    sources: SourceService
    actions: ActionService
    chat_store: VectorstoreChatStore
    chatkit_server: VectorstoreChatKitServer
    _closed: bool = False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.sources.close()
        await self.openai.close()
        await self.auth.close()
        await self.database.close()


def create_services(settings: AppSettings) -> AppServices:
    configure_logging(settings.log_level)
    database = DatabaseManager(settings)
    auth = AuthService(settings)
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
    )
    chat_store = VectorstoreChatStore(database=database, sources=sources)
    chatkit_server = VectorstoreChatKitServer(
        settings=settings,
        store=chat_store,
        sources=sources,
        actions=actions,
    )
    return AppServices(
        settings=settings,
        database=database,
        auth=auth,
        storage=storage,
        openai=openai,
        sources=sources,
        actions=actions,
        chat_store=chat_store,
        chatkit_server=chatkit_server,
    )
