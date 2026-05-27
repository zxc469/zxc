"""鉴权 API 层：注册 / 登录 / 刷新 / 登出 / 当前主体 / 超管创建管理员。

路由汇总（由上层 router.py 注册，最终路径会再加 /api/v1 前缀）：
- POST /auth/register/user   普通用户注册
- POST /auth/register/agent  客服人员注册
- POST /auth/login           统一登录
- POST /auth/refresh         刷新 access token（refresh 轮换）
- POST /auth/logout          登出（吊销 refresh）
- GET  /auth/me              当前登录主体视图（需 Bearer token）
- POST /auth/admins          由 super_admin 创建普通管理员（需 super_admin 鉴权）
注：管理员账号不开放匿名注册，初始账号由 V002 种子数据预置。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_principal, require_super_admin
from app.db.postgres_pool import get_db
from app.schemas.auth_models import (
    AdminCreateRequest,
    AgentRegisterRequest,
    LoginRequest,
    LogoutRequest,
    PrincipalView,
    RefreshRequest,
    RegisterResponse,
    TokenResponse,
    UserRegisterRequest,
)
from app.schemas.common_models import ApiResponse
from app.services import auth_service
from app.services.auth_service import AuthError
from app.utils.logger import get_logger
from app.utils.rate_limiter import (
    rate_limit_login_by_username,
    rate_limit_refresh,
    rate_limit_register,
)

logger = get_logger(__name__)

auth_router = APIRouter(prefix="/auth", tags=["auth"])


# ============================================================================
# 工具：统一异常映射 & 客户端审计信息提取
# ============================================================================
def _raise_from_auth_error(exc: AuthError) -> None:
    """将业务异常 AuthError 转为 FastAPI HTTPException"""
    raise HTTPException(
        status_code=exc.http_status,
        detail={"code": exc.code, "message": exc.message},
    )


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    """提取 User-Agent 与来源 IP（优先 X-Forwarded-For）用于 refresh token 审计"""
    user_agent = request.headers.get("user-agent")
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        ip_address = forwarded.split(",")[0].strip() or None
    else:
        ip_address = request.client.host if request.client else None
    return user_agent, ip_address


# ============================================================================
# 注册
# ============================================================================
@auth_router.post(
    "/register/user",
    response_model=ApiResponse[RegisterResponse],
    status_code=status.HTTP_201_CREATED,
)
async def register_user(
    payload: UserRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[RegisterResponse]:
    """
    【接口功能】普通用户注册
    参数：payload: UserRegisterRequest，含 username、password 及可选资料字段
    返回：RegisterResponse，含角色类型、主体 ID、账号
    异常：409: 账号已被注册；422: 入参校验失败；429: 频率限制
    """
    rate_limit_register(request)
    try:
        user = await auth_service.register_user(payload, db)
    except AuthError as exc:
        _raise_from_auth_error(exc)
    return ApiResponse(data=RegisterResponse(principal_type="user", principal_id=user.id, username=user.username))


@auth_router.post(
    "/register/agent",
    response_model=ApiResponse[RegisterResponse],
    status_code=status.HTTP_201_CREATED,
)
async def register_agent(
    payload: AgentRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[RegisterResponse]:
    """
    【接口功能】客服人员注册
    参数：payload: AgentRegisterRequest，含 username、password、real_name 等
    返回：RegisterResponse
    异常：409: 账号已被注册；422: 入参校验失败；429: 频率限制
    """
    rate_limit_register(request)
    try:
        agent = await auth_service.register_agent(payload, db)
    except AuthError as exc:
        _raise_from_auth_error(exc)
    return ApiResponse(data=RegisterResponse(principal_type="agent", principal_id=agent.id, username=agent.username))


@auth_router.post(
    "/admins",
    response_model=ApiResponse[RegisterResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_admin(
    payload: AdminCreateRequest,
    operator: PrincipalView = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[RegisterResponse]:
    """
    【接口功能】超级管理员后台创建普通管理员（需 super_admin 身份）
    参数：payload: AdminCreateRequest；operator: 由 require_super_admin 注入
    返回：RegisterResponse
    异常：401: 未登录；403: 非 super_admin；409: 账号不可用；422: 入参校验失败
    """
    try:
        admin = await auth_service.create_admin_by_super(payload, operator, db)
    except AuthError as exc:
        _raise_from_auth_error(exc)
    return ApiResponse(data=RegisterResponse(principal_type="admin", principal_id=admin.id, username=admin.username))


# ============================================================================
# 登录 / 刷新 / 登出
# ============================================================================
@auth_router.post("/login", response_model=ApiResponse[TokenResponse])
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[TokenResponse]:
    """
    【接口功能】统一登录接口，按 principal_type 验账号密码并签发双 token
    参数：payload: LoginRequest，含 username、password、principal_type
    返回：TokenResponse，含 access_token + refresh_token + 过期时间
    异常：401: 账号或密码错误；403: 账号已禁用；422: 入参校验失败；429: 频率限制
    """
    user_agent, ip_address = _client_meta(request)
    rate_limit_login_by_username(payload.username)
    try:
        return ApiResponse(data=await auth_service.login(payload, user_agent, ip_address, db))
    except AuthError as exc:
        _raise_from_auth_error(exc)


@auth_router.post("/refresh", response_model=ApiResponse[TokenResponse])
async def refresh_token(
    payload: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[TokenResponse]:
    """
    【接口功能】使用 refresh token 换取新的 access + refresh（旧 refresh 将被吊销）
    参数：payload: RefreshRequest，含 refresh_token 明文
    返回：TokenResponse，新的双 token
    异常：401: refresh token 无效/已吊销/已过期；403: 账号已禁用
    """
    user_agent, ip_address = _client_meta(request)
    rate_limit_refresh(request)
    try:
        return ApiResponse(data=await auth_service.refresh_access_token(payload.refresh_token, user_agent, ip_address, db))
    except AuthError as exc:
        _raise_from_auth_error(exc)


@auth_router.post("/logout", response_model=ApiResponse[dict])
async def logout(
    payload: LogoutRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """
    【接口功能】登出：吊销指定 refresh token（幂等）
    参数：payload: LogoutRequest，含 refresh_token 明文
    返回：ApiResponse
    异常：422: 入参校验失败
    """
    await auth_service.logout(payload.refresh_token, db)
    return ApiResponse(message="登出成功")


# ============================================================================
# 当前主体
# ============================================================================
@auth_router.get("/me", response_model=ApiResponse[PrincipalView])
async def read_me(
    principal: PrincipalView = Depends(get_current_principal),
) -> ApiResponse[PrincipalView]:
    """
    【接口功能】获取当前登录主体信息（需 Authorization: Bearer <access_token>）
    参数：无（从请求头取 token）
    返回：ApiResponse[PrincipalView]
    异常：401: token 缺失/非法/过期或主体不存在
    """
    return ApiResponse(data=principal)
