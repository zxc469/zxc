"""统一 API 响应模型。"""

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """所有 HTTP 接口统一响应 wrapper。

    data 为具体业务载荷，code 为业务状态码（200 表示成功），message 为人肉可读的提示信息。
    """

    code: int = 200
    message: str = "操作成功"
    data: T | None = None
