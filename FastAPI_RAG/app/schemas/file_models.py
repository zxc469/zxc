from pydantic import BaseModel

# 文件视图模型
class KnowledgeFileView(BaseModel):
    id: int
    file_name: str
    file_hash: str
    file_type: str
    file_size: int
    status: str
    total_chunks: int | None = None
    error_msg: str | None = None
    created_at: str
    updated_at: str

# 文件转换预览响应（不落库）
class FileConvertResponse(BaseModel):
    file_name: str
    file_hash: str
    file_size: int
    file_type: str
    markdown: str

# 文件入库请求
class FileIngestRequest(BaseModel):
    file_name: str
    file_hash: str
    file_size: int
    file_type: str
    markdown: str
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    chunk_separators: list[str] | None = None

# 分块预览请求
class FileChunkPreviewRequest(BaseModel):
    markdown: str
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    chunk_separators: list[str] | None = None


# 分块预览响应
class FileChunkPreviewResponse(BaseModel):
    total: int
    chunks: list[str]


# 已入库分块查看响应
class FileChunksViewResponse(BaseModel):
    file_name: str
    total: int
    chunks: list[str]
