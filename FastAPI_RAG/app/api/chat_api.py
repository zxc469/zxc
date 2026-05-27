"""
Agent 对话 API — 同步聊天入口。

本模块暴露 POST /chat/send 端点，作为用户与 Agent 对话的唯一 HTTP 入口。
调用链：接收请求体 → 注入 DB 会话 → chat_service.run_chat() → 返回 ChatResponse。

本模块仅做参数接收、依赖注入和异常转译，不访问数据库。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_principal, require_role
from app.db.postgres_pool import get_db
from app.schemas.auth_models import PrincipalView
from app.schemas.chat_models import ChatRequest, ChatResponse
from app.schemas.common_models import ApiResponse
from app.services.chat_service import run_chat
from app.utils.logger import get_logger
from app.utils.rate_limiter import rate_limit_chat

logger = get_logger(__name__)

chat_router = APIRouter(prefix="/chat", tags=["chat"])


@chat_router.post("/send", response_model=ApiResponse[ChatResponse])
async def chat_send(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
    principal: PrincipalView = Depends(require_role("user")),
) -> ApiResponse[ChatResponse]:
    """
    接收用户消息，存储入库，交由 Agent 处理后返回 AI 回复。

    数据库会话通过 FastAPI 依赖注入获取，传入 chat_service.run_chat()。

    Args:
        request: 请求体，包含 session_id、content（必填）、sender_id（选填）
        db: FastAPI 依赖注入的数据库会话

    Returns:
        ChatResponse，包含 Agent 生成的 answer 和可选的 ticket_id

    Raises:
        HTTPException 422: 参数校验失败（如 content 为空）
        HTTPException 500: Agent 执行异常或数据库写入失败等内部错误
    """
    rate_limit_chat(principal.principal_id)

    try:
        return ApiResponse(data=await run_chat(request, db=db))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("chat_send 异常: session_id=%s", request.session_id)
        raise HTTPException(status_code=500, detail="服务内部错误") from exc
