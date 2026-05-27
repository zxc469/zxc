"""简易内存限流器：按 IP / key 限制请求频率。

生产环境建议替换为 Redis 方案（如 slowapi + Redis backend），
当前内存实现适用于单进程部署场景。
"""

from __future__ import annotations

import time
import threading
from collections import defaultdict
from typing import Callable

from fastapi import Request, HTTPException, status


class RateLimiter:
    """基于滑动窗口的内存限流器。"""

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def _clean(self, key: str, now: float) -> None:
        cutoff = now - self._window
        self._buckets[key] = [t for t in self._buckets[key] if t > cutoff]

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            self._clean(key, now)
            if len(self._buckets[key]) >= self._max:
                return False
            self._buckets[key].append(now)
            return True

    def remaining(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            self._clean(key, now)
            return max(0, self._max - len(self._buckets[key]))


# ====================================================================
# 限流器实例（全局单例）
# ====================================================================

# 登录：每 IP 每分钟最多 6 次
login_ip_limiter = RateLimiter(max_requests=6, window_seconds=60)
# 登录：每用户名每分钟最多 5 次（防定向爆破）
login_username_limiter = RateLimiter(max_requests=5, window_seconds=60)
# 注册：每 IP 每分钟最多 3 次
register_limiter = RateLimiter(max_requests=3, window_seconds=60)
# refresh token：每 IP 每分钟最多 10 次
refresh_limiter = RateLimiter(max_requests=10, window_seconds=60)
# 聊天：每用户每分钟最多 30 次
chat_limiter = RateLimiter(max_requests=30, window_seconds=60)
# 文件上传/入库：每 IP 每分钟最多 10 次
file_upload_limiter = RateLimiter(max_requests=10, window_seconds=60)


# ====================================================================
# FastAPI 依赖工厂
# ====================================================================

def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit_login():
    """登录限流：按 IP + 用户名双重限制。"""
    async def _dependency(request: Request) -> None:
        ip = _client_ip(request)
        if not login_ip_limiter.is_allowed(ip):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"code": "RATE_LIMITED", "message": "请求过于频繁，请稍后再试"},
                headers={"Retry-After": "60"},
            )

        # 尝试从请求体中提取 username（FastAPI 中 body 尚未解析，这里做 best-effort）
        # 用户名限流在 endpoint 内手动调用，此处仅限 IP
    return _dependency


def rate_limit_login_by_username(username: str) -> None:
    """用户名维度限流：同一账号频繁尝试登录时拒绝。"""
    key = f"login_user:{username}"
    if not login_username_limiter.is_allowed(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "RATE_LIMITED", "message": "请求过于频繁，请稍后再试"},
            headers={"Retry-After": "60"},
        )


def rate_limit_register(request: Request) -> None:
    """注册限流：按 IP 限制。"""
    ip = _client_ip(request)
    if not register_limiter.is_allowed(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "RATE_LIMITED", "message": "注册请求过于频繁，请稍后再试"},
            headers={"Retry-After": "60"},
        )


def rate_limit_refresh(request: Request) -> None:
    """refresh token 限流：按 IP 限制。"""
    ip = _client_ip(request)
    if not refresh_limiter.is_allowed(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "RATE_LIMITED", "message": "请求过于频繁，请稍后再试"},
            headers={"Retry-After": "60"},
        )


def rate_limit_chat(user_id: int) -> None:
    """聊天限流：按用户 ID 限制。"""
    key = f"chat_user:{user_id}"
    if not chat_limiter.is_allowed(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "RATE_LIMITED", "message": "消息发送过于频繁，请稍后再试"},
            headers={"Retry-After": "60"},
        )


def rate_limit_file_upload(user_id: int) -> None:
    """文件上传限流：按用户 ID 限制。"""
    key = f"file_upload:{user_id}"
    if not file_upload_limiter.is_allowed(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "RATE_LIMITED", "message": "文件操作过于频繁，请稍后再试"},
            headers={"Retry-After": "60"},
        )
