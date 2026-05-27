import http from './http'

export interface FileConvertResponse {
  file_name: string
  file_hash: string
  file_size: number
  file_type: string
  markdown: string
}

export interface FileIngestRequest {
  file_name: string
  file_hash: string
  file_size: number
  file_type: string
  markdown: string
  chunk_size?: number
  chunk_overlap?: number
  chunk_separators?: string[]
}

export interface KnowledgeFileView {
  id: number
  file_name: string
  file_hash: string
  file_type: string
  file_size: number
  status: string
  total_chunks: number | null
  error_msg: string | null
  created_at: string
  updated_at: string
}

export interface FileChunkPreviewRequest {
  markdown: string
  chunk_size?: number
  chunk_overlap?: number
  chunk_separators?: string[]
}

export interface FileChunkPreviewResponse {
  total: number
  chunks: string[]
}

export function convertFile(file: File) {
  const form = new FormData()
  form.append('file', file)
  return http.post<FileConvertResponse>('/files/convert', form, {
    timeout: 60000, // PDF 转换耗时较长
  })
}

export function ingestFile(body: FileIngestRequest) {
  return http.post<KnowledgeFileView>('/files/ingest', body)
}

export function listFiles(limit = 100, offset = 0) {
  return http.get<KnowledgeFileView[]>('/files', { params: { limit, offset } })
}

export function previewChunks(body: FileChunkPreviewRequest) {
  return http.post<FileChunkPreviewResponse>('/files/chunk-preview', body)
}

export interface FileChunksViewResponse {
  file_name: string
  total: number
  chunks: string[]
}

export function getFileChunks(fileId: number) {
  return http.get<FileChunksViewResponse>(`/files/${fileId}/chunks`)
}

export function deleteFile(fileId: number) {
  return http.delete(`/files/${fileId}`)
}
