"""鉴权业务层：注册、登录、刷新、登出、当前主体解析。

【业务功能】串联 DAO、安全工具与 Pydantic 模型，实现三角色分离 + 双 token 完整鉴权流程
业务规则：
  1. 注册：校验 username 未被同类型角色占用；bcrypt 哈希密码；入库
  2. 登录：按 principal_type 路由到对应表验账号密码；签发 access+refresh；落库 refresh
  3. 刷新：校验 refresh token 存在、未吊销、未过期；签发新的 access（refresh 轮换）
  4. 登出：按 refresh token 明文吊销（设置 revoked_at）
  5. 当前主体：从 JWT 解码出 principal_type + principal_id，并从数据库验证主体有效性
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.data_access_service import auth_dao
from app.schemas.auth_models import (
    AdminCreateRequest,
    AgentRegisterRequest,
    LoginRequest,
    PrincipalView,
    TokenResponse,
    UserRegisterRequest,
)
from app.utils.logger import get_logger
from app.utils.security import (
    compute_refresh_expires_at,
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)

logger = get_logger(__name__)


class AuthError(Exception):
    """鉴权业务异常基类，携带业务错误码与 HTTP 友好提示。

    code：业务错误码，前端用于定向处理
    http_status：建议的 HTTP 状态码
    """

    def __init__(self, code: str, message: str, http_status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


# ============================================================================
# 注册
# ============================================================================
async def register_user(payload: UserRegisterRequest, db: AsyncSession) -> Any:
    """
    【业务功能】普通用户注册
    业务规则：1.username 在 users 表内唯一 2.密码 bcrypt 哈希后存储
                3.冲突时返回模糊错误以减小用户名枚举信号
    参数：payload: UserRegisterRequest；db: 数据库会话
    返回：UserORM，注册成功的用户记录
    异常：AuthError(USERNAME_UNAVAILABLE): 账号不可用
    """
    exists = await auth_dao.get_principal_by_username("user", payload.username, db)
    if exists is not None:
        raise AuthError("USERNAME_UNAVAILABLE", "该用户名无法使用，请更换后重试", http_status=409)
    password_hash = hash_password(payload.password)
    user = await auth_dao.create_user(
        username=payload.username,
        password_hash=password_hash,
        nickname=payload.nickname,
        email=payload.email,
        phone=payload.phone,
        avatar_url=payload.avatar_url,
        db=db,
    )
    logger.info("普通用户注册成功: id=%s username=%s", user.id, user.username)
    return user


async def register_agent(payload: AgentRegisterRequest, db: AsyncSession) -> Any:
    """
    【业务功能】客服人员注册
    业务规则：1.username 在 agents 表内唯一 2.密码 bcrypt 哈希后存储
                3.冲突时返回模糊错误以减小用户名枚举信号
                4.自动创建会话配额记录（max_sessions 从注册参数获取）
    参数：payload: AgentRegisterRequest；db: 数据库会话
    返回：AgentORM，注册成功的客服记录
    异常：AuthError(USERNAME_UNAVAILABLE): 账号不可用
    """
    exists = await auth_dao.get_principal_by_username("agent", payload.username, db)
    if exists is not None:
        raise AuthError("USERNAME_UNAVAILABLE", "该用户名无法使用，请更换后重试", http_status=409)
    password_hash = hash_password(payload.password)
    agent = await auth_dao.create_agent(
        username=payload.username,
        password_hash=password_hash,
        real_name=payload.real_name,
        email=payload.email,
        phone=payload.phone,
        department=payload.department,
        max_sessions=payload.max_sessions,
        db=db,
    )
    
    # 自动创建会话配额记录
    from app.data_access_service.session_dao import create_or_update_agent_quota
    await create_or_update_agent_quota(
        agent_id=agent.id,
        max_sessions=payload.max_sessions,
        db=db,
    )
    
    logger.info("客服人员注册成功: id=%s username=%s", agent.id, agent.username)
    return agent


async def create_admin_by_super(
    payload: AdminCreateRequest,
    operator: PrincipalView,
    db: AsyncSession,
) -> Any:
    """
    【业务功能】由 super_admin 后台创建普通管理员账号
    业务规则：
      1. 操作者必须为 admin 且 role_level=super_admin，否则拒绝（二重保险）
      2. username 在 admins 表内唯一；密码 bcrypt 哈希后存储
      3. 新建管理员 role_level 固定为 "admin"（super_admin 不开放运行时创建）
    参数：payload: AdminCreateRequest；operator: 当前 super_admin 视图；db: 数据库会话
    返回：AdminORM，新建的管理员记录
    异常：
      - AuthError(PERMISSION_DENIED): 操作者不是 super_admin
      - AuthError(USERNAME_UNAVAILABLE): 账号不可用
    """
    if operator.principal_type != "admin" or operator.role_level != "super_admin":
        raise AuthError("PERMISSION_DENIED", "仅超级管理员可创建管理员账号", http_status=403)

    exists = await auth_dao.get_principal_by_username("admin", payload.username, db)
    if exists is not None:
        raise AuthError("USERNAME_UNAVAILABLE", "该用户名无法使用，请更换后重试", http_status=409)

    password_hash = hash_password(payload.password)
    admin = await auth_dao.create_admin(
        username=payload.username,
        password_hash=password_hash,
        real_name=payload.real_name,
        email=payload.email,
        role_level="admin",
        db=db,
    )
    logger.info(
        "超级管理员创建管理员成功: operator_id=%s new_admin_id=%s username=%s",
        operator.principal_id,
        admin.id,
        admin.username,
    )
    return admin


# ============================================================================
# 登录 / 刷新 / 登出
# ============================================================================
def _check_principal_active(principal_type: str, principal: Any) -> None:
    """校验主体账号状态是否允许登录 / 使用（disabled 禁止）"""
    status = getattr(principal, "status", None)
    if status == "disabled":
        raise AuthError("ACCOUNT_DISABLED", "账号已禁用", http_status=403)


async def _issue_tokens(
    principal_type: str,
    principal_id: int,
    user_agent: str | None,
    ip_address: str | None,
    db: AsyncSession,
) -> TokenResponse:
    """为指定主体同时签发 access + refresh token 并落库 refresh 记录"""
    access_token, expires_in = create_access_token(principal_type, principal_id)
    refresh_plain = generate_refresh_token()
    await auth_dao.create_refresh_token(
        principal_type=principal_type,
        principal_id=principal_id,
        token_hash=hash_refresh_token(refresh_plain),
        expires_at=compute_refresh_expires_at(),
        user_agent=user_agent,
        ip_address=ip_address,
        db=db,
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_plain,
        expires_in=expires_in,
        principal_type=principal_type,  # type: ignore[arg-type]
        principal_id=principal_id,
    )


async def login(
    payload: LoginRequest,
    user_agent: str | None,
    ip_address: str | None,
    db: AsyncSession,
) -> TokenResponse:
    """
    【业务功能】统一登录：按 principal_type 验证账号密码并签发双 token
    业务规则：
      1. 按 principal_type 在对应表中查 username
      2. 校验密码 bcrypt
      3. 校验账号状态非 disabled
      4. 更新 last_login_at
      5. 签发 access + refresh，并落库 refresh
    参数：payload: LoginRequest；user_agent/ip_address: 审计字段；db: 数据库会话
    返回：TokenResponse，含双 token
    异常：
      - AuthError(INVALID_CREDENTIALS): 账号或密码错误（不区分以防用户名枚举）
      - AuthError(ACCOUNT_DISABLED): 账号已禁用
    """
    principal = await auth_dao.get_principal_by_username(payload.principal_type, payload.username, db)
    if principal is None or not verify_password(payload.password, principal.password_hash):
        raise AuthError("INVALID_CREDENTIALS", "账号或密码错误", http_status=401)
    _check_principal_active(payload.principal_type, principal)
    await auth_dao.update_last_login(payload.principal_type, principal.id, db)
    logger.info("登录成功: type=%s id=%s username=%s", payload.principal_type, principal.id, principal.username)
    return await _issue_tokens(payload.principal_type, principal.id, user_agent, ip_address, db)


async def refresh_access_token(
    refresh_token: str,
    user_agent: str | None,
    ip_address: str | None,
    db: AsyncSession,
) -> TokenResponse:
    """
    【业务功能】使用 refresh token 换取新的 access + refresh（refresh 轮换）
    业务规则：
      1. 按 SHA256 摘要查库
      2. 校验：存在、未吊销、未过期
      3. 校验对应主体仍存在且账号状态非 disabled
      4. 吊销旧 refresh；签发并落库新 refresh；签发新 access
    参数：refresh_token: 明文；user_agent/ip_address: 审计；db: 数据库会话
    返回：TokenResponse，新的双 token
    异常：AuthError(INVALID_REFRESH_TOKEN): 无效、已吊销或已过期；AuthError(ACCOUNT_DISABLED)
    """
    token_hash = hash_refresh_token(refresh_token)
    record = await auth_dao.get_refresh_token_by_hash(token_hash, db)
    if record is None or record.revoked_at is not None:
        raise AuthError("INVALID_REFRESH_TOKEN", "刷新令牌无效或已吊销", http_status=401)
    if record.expires_at <= datetime.now(timezone.utc):
        raise AuthError("INVALID_REFRESH_TOKEN", "刷新令牌已过期", http_status=401)

    principal = await auth_dao.get_principal_by_id(record.principal_type, record.principal_id, db)
    if principal is None:
        raise AuthError("INVALID_REFRESH_TOKEN", "主体不存在", http_status=401)
    _check_principal_active(record.principal_type, principal)

    # refresh token 轮换：对旧 refresh 做 CAS 小原子封闭——必须从未吊销 -> 吊销
    # 成功同样记录在本请求所属连接内，避免并发轮换时同一 refresh token 被两次换取 token
    revoked_count = await auth_dao.revoke_refresh_token_by_hash(token_hash, db)
    if revoked_count == 0:
        # 并发竞争中本请求输了：说明另一进程已先行消费掉该 refresh。
        # 为防重放攻击，同时将该主体名下所有活跃 refresh 全部吊销（强制登出）
        try:
            await auth_dao.revoke_all_principal_tokens(record.principal_type, record.principal_id, db)
        except Exception as exc:  # 尽力补偿：清理失败不影响主流程，但必须留下告警日志
            logger.warning(
                "refresh 轮换 CAS 命中并发，强制登出补偿失败: type=%s id=%s err=%s",
                record.principal_type,
                record.principal_id,
                exc,
            )
        logger.warning(
            "检测到 refresh token 并发重放: type=%s id=%s",
            record.principal_type,
            record.principal_id,
        )
        raise AuthError("INVALID_REFRESH_TOKEN", "刷新令牌无效或已吊销", http_status=401)

    logger.info("刷新令牌成功: type=%s id=%s", record.principal_type, record.principal_id)
    return await _issue_tokens(record.principal_type, record.principal_id, user_agent, ip_address, db)


async def logout(refresh_token: str, db: AsyncSession) -> None:
    """
    【业务功能】登出：吊销指定 refresh token
    业务规则：幂等操作——即使 token 不存在或已吊销也返回成功，不泄露细节
    参数：refresh_token: 明文；db: 数据库会话
    返回：None
    异常：无
    """
    token_hash = hash_refresh_token(refresh_token)
    revoked = await auth_dao.revoke_refresh_token_by_hash(token_hash, db)
    logger.info("登出处理完成: 吊销记录数=%s", revoked)


# ============================================================================
# 当前主体解析（供依赖使用）
# ============================================================================
async def get_principal_view(
    principal_type: str,
    principal_id: int,
    db: AsyncSession,
) -> PrincipalView:
    """
    【业务功能】按 (principal_type, principal_id) 返回主体视图
    业务规则：display_name 取 nickname(user) / real_name(agent/admin)
    参数：principal_type、principal_id、db
    返回：PrincipalView
    异常：AuthError(PRINCIPAL_NOT_FOUND): 主体不存在
    """
    principal = await auth_dao.get_principal_by_id(principal_type, principal_id, db)
    if principal is None:
        raise AuthError("PRINCIPAL_NOT_FOUND", "主体不存在", http_status=401)

    if principal_type == "user":
        display_name = principal.nickname
    else:
        display_name = getattr(principal, "real_name", None)

    return PrincipalView(
        principal_type=principal_type,  # type: ignore[arg-type]
        principal_id=principal.id,
        username=principal.username,
        display_name=display_name,
        email=getattr(principal, "email", None),
        status=principal.status,
        role_level=getattr(principal, "role_level", None) if principal_type == "admin" else None,
    )
