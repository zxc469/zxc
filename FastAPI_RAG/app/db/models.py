"""postgre_service 的数据模型。

作用：
- `KnowledgeFileRecord`：数据库查询结果的统一返回结构；
- `SaveFileMetaInput`：保存文件元数据时的入参结构。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

class SaveFileMetaInput(BaseModel):
    """保存文件元数据时的入参结构"""

    file_name: str = Field(..., min_length=1)
    file_hash: str = Field(..., min_length=64, max_length=64)
    file_type: str = Field(..., min_length=1, max_length=20)
    file_size: int = Field(..., ge=0)
    status: Literal["processing", "success", "failed"] = "processing"
    total_chunks: int | None = Field(default=None, ge=0)
    error_msg: str | None = None
    
class KnowledgeFileRecord(BaseModel):
    """数据库查询结果的统一返回结构"""

    id: int # 文件 ID
    file_name: str # 文件名
    file_hash: str # 文件哈希
    file_type: str # 文件类型
    file_size: int # 文件大小
    status: str # 状态
    total_chunks: int | None = None # 总行数
    error_msg: str | None = None # 错误信息
    created_at: datetime # 创建时间
    updated_at: datetime # 更新时间




