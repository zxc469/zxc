"""数据库服务层：连接池、客户端、ORM 模型与数据传输模型。"""

from app.db.orm import (
    AdminORM,
    AgentORM,
    AgentSessionQuotaORM,
    Base,
    ChatSessionORM,
    KnowledgeFileORM,
    RefreshTokenORM,
    SessionMessageORM,
    UserORM,
)
from app.db.models import KnowledgeFileRecord, SaveFileMetaInput
from app.db.session_models import (
    AgentQuotaInput,
    AgentQuotaRecord,
    MessageCreateInput,
    MessageRecord,
    SessionCreateInput,
    SessionRecord,
    SessionUpdateInput,
)
from app.db.postgres_pool import PostgresPoolManager, get_db, postgres_pool_manager
from app.db.qdrant_client import (
    QdrantClientFactory,
    QdrantConnectionService,
    QdrantVectorService,
    create_qdrant_async_client,
)

__all__ = [
    # ORM
    "Base",
    "KnowledgeFileORM",
    "UserORM",
    "AgentORM",
    "AdminORM",
    "RefreshTokenORM",
    "ChatSessionORM",
    "SessionMessageORM",
    "AgentSessionQuotaORM",
    # Pydantic models
    "KnowledgeFileRecord",
    "SaveFileMetaInput",
    "SessionCreateInput",
    "SessionUpdateInput",
    "SessionRecord",
    "MessageCreateInput",
    "MessageRecord",
    "AgentQuotaInput",
    "AgentQuotaRecord",
    # Postgres pool
    "PostgresPoolManager",
    "postgres_pool_manager",
    "get_db",
    # Qdrant client
    "QdrantClientFactory",
    "QdrantConnectionService",
    "QdrantVectorService",
    "create_qdrant_async_client",
]
