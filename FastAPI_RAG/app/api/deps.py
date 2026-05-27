"""API 层通用鉴权依赖。

【工具功能】为受保护路由提供两类依赖：
- get_current_principal：从 Authorization: Bearer <access_token> 解析 JWT，返回当前主体视图
- require_role：角色白名单工厂函数，限制仅指定角色可访问
"""

from __future__ import annotations

from typing import Callable

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres_pool import get_db
from app.schemas.auth_models import PrincipalType, PrincipalView
from app.services import auth_service
from app.services.auth_service import AuthError
from app.utils.security import decode_access_token


# ============================================================================
# 统一 401 构造
# ============================================================================
def _unauthorized(code: str, message: str) -> HTTPException:
    """构造标准 401 响应（含 WWW-Authenticate 头，方便客户端识别）"""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": code, "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _parse_bearer_token(authorization: str | None) -> str:
    """
    【工具功能】从 Authorization 头解析出 Bearer token
    支持：格式 "Bearer <token>"，大小写不敏感
    参数：authorization: 原始头部值（可能为 None）
    返回：token 字符串
    异常：HTTPException 401: 头部缺失或格式非法
    """
    if not authorization:
        raise _unauthorized("MISSING_TOKEN", "缺少认证凭证")
    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise _unauthorized("INVALID_AUTH_HEADER", "Authorization 头格式非法")
    return parts[1]


# ============================================================================
# 当前主体依赖
# ============================================================================
async def get_current_principal(
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> PrincipalView:
    """
    【接口功能】解析 access token 得到当前登录主体；供受保护路由使用
    参数：authorization: Authorization 头；db: 数据库会话
    返回：PrincipalView，当前登录主体视图
    异常：HTTPException 401: token 缺失、非法、已过期或主体不存在
    """
    token = _parse_bearer_token(authorization)
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("TOKEN_EXPIRED", "访问令牌已过期") from exc
    except jwt.InvalidTokenError as exc:
        raise _unauthorized("INVALID_TOKEN", "访问令牌无效") from exc

    principal_type = payload.get("principal_type")
    principal_id = payload.get("principal_id")
    if principal_type not in ("user", "agent", "admin") or not isinstance(principal_id, int):
        raise _unauthorized("INVALID_TOKEN", "访问令牌载荷非法")

    try:
        return await auth_service.get_principal_view(principal_type, principal_id, db)
    except AuthError as exc:
        raise _unauthorized(exc.code, exc.message) from exc


# ============================================================================
# 角色白名单依赖工厂
# ============================================================================
def require_role(*roles: PrincipalType) -> Callable[[PrincipalView], PrincipalView]:
    """
    【工具功能】生成角色白名单依赖：仅当当前主体角色在 roles 集合内才放行
    支持：任意 user / agent / admin 组合
    参数：*roles: 允许的角色集合
    返回：FastAPI 依赖函数
    异常：HTTPException 403: 当前主体角色不在白名单
    """
    allowed: set[str] = {r for r in roles}

    async def _dependency(principal: PrincipalView = Depends(get_current_principal)) -> PrincipalView:
        if principal.principal_type not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "FORBIDDEN", "message": "当前角色无权访问该资源"},
            )
        return principal

    return _dependency


# ============================================================================
# 超级管理员专用依赖
# ============================================================================
async def require_super_admin(
    principal: PrincipalView = Depends(get_current_principal),
) -> PrincipalView:
    """
    【工具功能】仅当前主体为 admin 且 role_level=super_admin 时放行
    支持：依赖式注入到需要超级权限的路由
    参数：principal: 当前登录主体视图
    返回：PrincipalView（校验通过后直接继承）
    异常：HTTPException 403: 非 super_admin 或权限信息缺失
    """
    if principal.principal_type != "admin" or principal.role_level != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "仅超级管理员可访问该资源"},
        )
    return principal
