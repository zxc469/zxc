"""鉴权模块 Pydantic 模型。

涵盖：
- 注册请求（三角色各自字段裁剪）
- 登录请求
- 刷新令牌请求
- Token 响应（含 access_token + refresh_token）
- 主体视图（供 /me 接口返回）
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field

PrincipalType = Literal["user", "agent", "admin"]


# ============================================================================
# 注册请求
# ============================================================================
class UserRegisterRequest(BaseModel):
    """普通用户注册入参"""

    username: str = Field(..., min_length=3, max_length=64, description="登录账号")
    password: str = Field(..., min_length=6, max_length=128, description="登录密码（明文，仅传输层使用）")
    nickname: str | None = Field(default=None, max_length=64)
    email: EmailStr | None = Field(default=None)
    phone: str | None = Field(default=None, max_length=32)
    avatar_url: str | None = Field(default=None, max_length=512)


class AgentRegisterRequest(BaseModel):
    """客服人员注册入参"""

    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    real_name: str = Field(..., min_length=1, max_length=64)
    email: EmailStr | None = Field(default=None)
    phone: str | None = Field(default=None, max_length=32)
    department: str | None = Field(default=None, max_length=64)
    max_sessions: int = Field(default=5, ge=0, le=100)


class AdminCreateRequest(BaseModel):
    """管理员创建入参（仅 super_admin 后台创建使用）

    注意：
      - 为防止权限垣墙被穿透，本接口仅支持创建普通管理员 (role_level="admin")，
        super_admin 类别由数据库种子数据预置，不开放运行时创建
      - 因此不接受客户端传入的 role_level 字段
    """

    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    real_name: str = Field(..., min_length=1, max_length=64)
    email: EmailStr | None = Field(default=None)


# ============================================================================
# 登录 / 刷新 / 登出
# ============================================================================
class LoginRequest(BaseModel):
    """统一登录入参：username + password + 角色类型"""

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)
    principal_type: PrincipalType = Field(..., description="登录角色：user / agent / admin")


class RefreshRequest(BaseModel):
    """刷新 token 入参：仅需提供 refresh_token 明文"""

    refresh_token: str = Field(..., min_length=16, max_length=512)


class LogoutRequest(BaseModel):
    """登出入参：吊销指定 refresh token"""

    refresh_token: str = Field(..., min_length=16, max_length=512)


# ============================================================================
# 响应模型
# ============================================================================
class TokenResponse(BaseModel):
    """登录/刷新成功后返回给前端的双 token 结构"""

    access_token: str = Field(..., description="短期业务凭证（JWT），放在 Authorization: Bearer 头")
    refresh_token: str = Field(..., description="长期刷新凭证，仅用于换取新 access_token")
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int = Field(..., description="access_token 有效期（秒）")
    principal_type: PrincipalType
    principal_id: int


class RegisterResponse(BaseModel):
    """注册成功响应数据"""

    principal_type: PrincipalType
    principal_id: int
    username: str


class PrincipalView(BaseModel):
    """当前登录主体基础视图（用于 /auth/me）"""

    principal_type: PrincipalType
    principal_id: int
    username: str
    display_name: str | None = None
    email: str | None = None
    status: str
    # 仅 admin 主体会填充 role_level (super_admin / admin)，用于前端和后端权限细分
    role_level: str | None = None


