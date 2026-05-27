"""安全工具：密码哈希、JWT 签发校验、token 摘要。

【工具功能】统一封装鉴权相关底层安全原语
支持：
- bcrypt 密码哈希与校验
- JWT（HS256/HS512 等）签发与解码
- refresh token 的随机生成与 SHA256 摘要
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.config.settings import settings


# ============================================================================
# 密码哈希
# ============================================================================
def hash_password(password: str) -> str:
    """
    【工具功能】使用 bcrypt 计算密码哈希
    支持：任意明文字符串（UTF-8 编码 <= 72 字节，超长会被 bcrypt 自动截断）
    参数：password: 明文密码
    返回：bcrypt 哈希字符串（含算法/cost/salt，长度约 60）
    异常：ValueError: 明文为空
    """
    if not password:
        raise ValueError("密码不能为空")
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12))
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """
    【工具功能】校验明文密码与数据库中 bcrypt 哈希是否匹配
    支持：任意非空明文 + bcrypt 哈希字符串
    参数：password: 用户提交明文；password_hash: 数据库存储哈希
    返回：bool，True 表示匹配
    异常：无（格式异常时返回 False，避免泄露细节）
    """
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ============================================================================
# JWT access token
# ============================================================================
def create_access_token(principal_type: str, principal_id: int, extra: dict[str, Any] | None = None) -> tuple[str, int]:
    """
    【工具功能】为指定主体签发 JWT access token
    支持：三角色主体（user / agent / admin）
    参数：principal_type: 主体类型；principal_id: 主体 ID；extra: 可选的额外 claims
    返回：(token_str, expires_in_seconds) 元组
    异常：无（使用配置项默认密钥/算法/过期时间）
    """
    expires_delta = timedelta(minutes=settings.access_token_expire_minutes)
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(principal_id),                # JWT 标准字段：主体 ID
        "principal_type": principal_type,         # 自定义字段：主体类型
        "principal_id": principal_id,             # 冗余字段：便于直接读取
        "iat": int(now.timestamp()),              # 签发时间
        "exp": int((now + expires_delta).timestamp()),  # 过期时间
        "type": "access",                         # 令牌类型标记，防止混用
    }
    if extra:
        payload.update(extra)
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str) -> dict[str, Any]:
    """
    【工具功能】解码并校验 JWT access token
    支持：HS256 等对称算法；自动校验签名与过期
    参数：token: JWT 字符串（不含 "Bearer " 前缀）
    返回：dict，解码后的 payload（含 principal_type、principal_id 等）
    异常：
      - jwt.ExpiredSignatureError: token 已过期
      - jwt.InvalidTokenError: 签名非法/格式错误/类型错误
    """
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("非 access token")
    return payload


# ============================================================================
# Refresh token
# ============================================================================
def generate_refresh_token() -> str:
    """
    【工具功能】生成一个高熵随机 refresh token（明文，仅返回给前端一次）
    支持：URL-safe base64 编码，默认 64 字节熵 ≈ 86 字符
    参数：无
    返回：str，明文 refresh token
    异常：无
    """
    return secrets.token_urlsafe(64)


def hash_refresh_token(token: str) -> str:
    """
    【工具功能】对 refresh token 明文取 SHA256 摘要，用于数据库存储与比对
    支持：任意非空字符串
    参数：token: refresh token 明文
    返回：64 字符的十六进制 SHA256 摘要
    异常：ValueError: token 为空
    """
    if not token:
        raise ValueError("token 不能为空")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def compute_refresh_expires_at() -> datetime:
    """
    【工具功能】根据配置计算 refresh token 的过期时间（带时区）
    支持：读取 settings.refresh_token_expire_days
    参数：无
    返回：datetime，UTC 时区的到期时间
    异常：无
    """
    return datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
