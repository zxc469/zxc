from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_principal, require_role
from app.db.postgres_pool import get_db
from app.db.models import KnowledgeFileRecord
from app.data_access_service.dao import get_file_by_id as dao_get_file_by_id
from app.schemas.auth_models import PrincipalView
from app.schemas.common_models import ApiResponse
from app.schemas.file_models import FileChunkPreviewRequest, FileChunkPreviewResponse, FileChunksViewResponse, FileConvertResponse, FileIngestRequest, KnowledgeFileView
from app.services.knowledge_file_service import get_knowledge_file_service
from app.services.agent.vector_service import VectorIngestService, get_vector_ingest_service
from app.utils.logger import get_logger
from app.utils.rate_limiter import rate_limit_file_upload

logger = get_logger(__name__)

files_router = APIRouter(prefix="/files", tags=["files"])

# 文件视图模型转换函数
def _to_view(record: KnowledgeFileRecord) -> KnowledgeFileView:
    """将 KnowledgeFileRecord 转换为 KnowledgeFileView"""
    return KnowledgeFileView(
        id=record.id,
        file_name=record.file_name,
        file_hash=record.file_hash,
        file_type=record.file_type,
        file_size=record.file_size,
        status=record.status,
        total_chunks=record.total_chunks,
        error_msg=record.error_msg,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
    )

# 文件转换预览接口（不落库）
@files_router.post("/convert", response_model=ApiResponse[FileConvertResponse])
async def async_convert_file(
    file: UploadFile = File(...),
    principal: PrincipalView = Depends(require_role("admin")),
) -> ApiResponse[FileConvertResponse]:
    """
    【接口功能】上传文件并转换为 Markdown 文本，仅预览，不写入数据库
    参数：file: 上传的文件（multipart/form-data）
    返回：ApiResponse[FileConvertResponse]
    异常：422: 文件格式/大小不合规或转换失败；500: 服务内部错误
    """
    rate_limit_file_upload(principal.principal_id)
    try:
        file_name, file_hash, file_size, file_type, markdown = await get_knowledge_file_service().async_convert_file(file)
        return ApiResponse(data=FileConvertResponse(file_name=file_name, file_hash=file_hash, file_size=file_size, file_type=file_type, markdown=markdown))
    except ValueError as exc:
        # 业务异常（文件格式错误、大小超限等）可以直接返回给前端
        raise HTTPException(status_code=422, detail={"code": "FILE_CONVERT_FAILED", "message": str(exc)}) from exc
    except Exception as exc:
        logger.exception("文件转换异常: file_name=%s", file.filename)
        raise HTTPException(status_code=500, detail={"code": "FILE_CONVERT_FAILED", "message": "文件转换失败，请稍后重试。"}) from exc

# 分块预览接口 — 使用与入库完全一致的分割器
@files_router.post("/chunk-preview", response_model=ApiResponse[FileChunkPreviewResponse])
async def chunk_preview(
    body: FileChunkPreviewRequest,
    principal: PrincipalView = Depends(require_role("admin")),
) -> ApiResponse[FileChunkPreviewResponse]:
    """
    【接口功能】对 Markdown 文本按给定参数分块，返回与入库时一致的分块结果
    参数：body: FileChunkPreviewRequest，含 markdown 及可选切分参数
    返回：ApiResponse[FileChunkPreviewResponse]
    异常：422: 切块为空或参数不合法
    """
    try:
        splitter = VectorIngestService._build_splitter(body.chunk_size, body.chunk_overlap, body.chunk_separators)
        chunks = splitter.split_text(body.markdown)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "CHUNK_INVALID", "message": str(exc)}) from exc
    if not chunks:
        raise HTTPException(status_code=422, detail={"code": "CHUNK_EMPTY", "message": "切块结果为空，请检查参数或文档内容。"})
    return ApiResponse(data=FileChunkPreviewResponse(total=len(chunks), chunks=chunks))

# 文件入库接口
@files_router.post("/ingest", response_model=ApiResponse[KnowledgeFileView])
async def async_ingest_file(
    body: FileIngestRequest,
    db: AsyncSession = Depends(get_db),
    principal: PrincipalView = Depends(require_role("admin")),
) -> ApiResponse[KnowledgeFileView]:
    """
    【接口功能】将已转换的文件内容向量化并写入知识库（Qdrant + PostgreSQL）
    参数：body: FileIngestRequest，含文件名、哈希、大小、类型及 Markdown 内容
    返回：ApiResponse[KnowledgeFileView]
    异常：422: 切块为空等业务异常；500: 向量化或数据库写入失败
    """
    rate_limit_file_upload(principal.principal_id)
    try:
        _, message, record = await get_knowledge_file_service().async_ingest_file(
            body.file_name, body.file_hash, body.file_size, body.file_type, body.markdown, db,
            chunk_size=body.chunk_size, chunk_overlap=body.chunk_overlap, chunk_separators=body.chunk_separators,
        )
        return ApiResponse(message=message, data=_to_view(record))
    except ValueError as exc:
        # 业务异常（切块为空等）可直接返回给前端
        raise HTTPException(status_code=422, detail={"code": "FILE_INGEST_FAILED", "message": str(exc)}) from exc
    except Exception as exc:
        logger.exception("文件入库异常: file_name=%s", body.file_name)
        raise HTTPException(status_code=500, detail={"code": "FILE_INGEST_FAILED", "message": "文件入库失败，请稍后重试或联系管理员。"}) from exc

# 文件列表接口
@files_router.get("", response_model=ApiResponse[list[KnowledgeFileView]])
async def async_list_files(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    principal: PrincipalView = Depends(require_role("admin")),
) -> ApiResponse[list[KnowledgeFileView]]:
    """
    【接口功能】分页查询知识库文件列表，按上传时间倒序排列
    参数：limit: 每页数量（1~500，默认100）；offset: 分页偏移（默认0）
    返回：ApiResponse[list[KnowledgeFileView]]
    异常：500: 数据库查询失败
    """
    records = await get_knowledge_file_service().async_list_files(db, limit=limit, offset=offset)
    return ApiResponse(data=[_to_view(record) for record in records])

# 文件删除接口
@files_router.delete("/{file_id}", response_model=ApiResponse[KnowledgeFileView])
async def async_delete_file(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    principal: PrincipalView = Depends(require_role("admin")),
) -> ApiResponse[KnowledgeFileView]:
    """
    【接口功能】根据文件 ID 删除知识库文件记录
    参数：file_id: 文件 ID
    返回：ApiResponse[KnowledgeFileView]，不存在时 data 为 null
    异常：500: 数据库删除失败
    """
    record = await get_knowledge_file_service().async_delete_file_by_id(file_id, db)
    if record is None:
        return ApiResponse(message="文件不存在。", data=None)
    return ApiResponse(message="文件删除成功。", data=_to_view(record))


# 查看已入库文件的分块内容
@files_router.get("/{file_id}/chunks", response_model=ApiResponse[FileChunksViewResponse])
async def view_file_chunks(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    principal: PrincipalView = Depends(require_role("admin")),
) -> ApiResponse[FileChunksViewResponse]:
    """
    【接口功能】查看已入库文件的分块内容，从 Qdrant 按 chunk_index 排序返回
    参数：file_id: 文件 ID
    返回：ApiResponse[FileChunksViewResponse]
    异常：404: 文件不存在；500: Qdrant 查询失败
    """
    file_record = await dao_get_file_by_id(file_id, db)
    if file_record is None:
        raise HTTPException(status_code=404, detail={"code": "FILE_NOT_FOUND", "message": "文件不存在。"})
    try:
        chunks = await get_vector_ingest_service().get_chunks_by_file_hash(file_record.file_hash)
        return ApiResponse(data=FileChunksViewResponse(file_name=file_record.file_name, total=len(chunks), chunks=chunks))
    except Exception as exc:
        logger.exception("查看分块异常: file_id=%s", file_id)
        raise HTTPException(status_code=500, detail={"code": "CHUNK_VIEW_FAILED", "message": "分块查询失败。"}) from exc
