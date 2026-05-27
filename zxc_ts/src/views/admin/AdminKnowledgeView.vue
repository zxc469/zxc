<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import {
  convertFile, ingestFile, listFiles, deleteFile, previewChunks, getFileChunks,
  type FileConvertResponse, type KnowledgeFileView,
} from '@/api/files'

// ---- 默认切分参数（与后端 langchain RecursiveCharacterTextSplitter 一致） ----
const DEFAULT_CHUNK_SIZE = 1000
const DEFAULT_CHUNK_OVERLAP = 200
const DEFAULT_SEPARATORS = ['\n\n', '\n', ' ', '']

// ---- 预览对话框 ----
const previewVisible = ref(false)
const editableMarkdown = ref('')
const ingesting = ref(false)
const convertResult = ref<FileConvertResponse | null>(null)
const converting = ref(false)

// ---- 文件列表 ----
const fileList = ref<KnowledgeFileView[]>([])
const loadingList = ref(false)

// ---- 切分参数 ----
const chunkSize = ref(DEFAULT_CHUNK_SIZE)
const chunkOverlap = ref(DEFAULT_CHUNK_OVERLAP)
const separatorsText = ref(separatorsToDisplay(DEFAULT_SEPARATORS))

// ---- 后端返回的 chunk 列表 ----
const chunks = ref<string[]>([])
const totalChunks = ref(0)
const chunking = ref(false)

function separatorsToDisplay(seps: string[]): string {
  return seps.map(s => s.replace(/\\/g, '\\\\').replace(/\n/g, '\\n')).join(',')
}

function parseSeparators(input: string): string[] {
  return input.split(',').map(s => {
    return s.replace(/\\\\/g, '\x00').replace(/\\n/g, '\n').replace(/\x00/g, '\\')
  })
}

function extractApiError(e: any, fallback: string): string {
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && typeof detail.message === 'string') return detail.message
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0]
    if (first?.msg) return first.msg
    return JSON.stringify(detail)
  }
  return fallback
}

function validateSeparators(input: string): string | null {
  if (/，/.test(input)) return '分隔符之间请使用英文逗号 "," 而非中文逗号 "，"'
  const parts = input.split(',')
  for (const p of parts) {
    const m = p.match(/\\+$/)
    if (m && m[0].length % 2 !== 0) return `"${p}" 中转义符 \\ 不成对，请用 \\\\ 表示反斜杠本身`
  }
  return null
}

const separatorsError = ref<string | null>(null)

function resetChunkParams() {
  chunkSize.value = DEFAULT_CHUNK_SIZE
  chunkOverlap.value = DEFAULT_CHUNK_OVERLAP
  separatorsText.value = separatorsToDisplay(DEFAULT_SEPARATORS)
  separatorsError.value = null
}

// ---- 调用后端分块预览 ----
let _previewId = 0
let _previewTimer: ReturnType<typeof setTimeout> | null = null

async function fetchChunkPreview() {
  const id = ++_previewId
  const text = editableMarkdown.value
  if (!text) {
    chunks.value = []
    totalChunks.value = 0
    return
  }

  chunking.value = true
  try {
    const { data } = await previewChunks({
      markdown: text,
      chunk_size: chunkSize.value,
      chunk_overlap: chunkOverlap.value,
      chunk_separators: parseSeparators(separatorsText.value),
    })
    if (id !== _previewId) return
    chunks.value = data.chunks
    totalChunks.value = data.total
  } catch {
    if (id === _previewId) chunks.value = []
  } finally {
    if (id === _previewId) chunking.value = false
  }
}

function scheduleChunkPreview() {
  if (_previewTimer) clearTimeout(_previewTimer)
  _previewTimer = setTimeout(() => fetchChunkPreview(), 300)
}

watch([editableMarkdown, separatorsText, chunkSize, chunkOverlap], () => {
  scheduleChunkPreview()
}, { deep: false })

watch(previewVisible, (visible) => {
  if (!visible) {
    convertResult.value = null
    editableMarkdown.value = ''
    chunks.value = []
    totalChunks.value = 0
  }
})

// ---- 分块渲染 ----
const chunkColors = [
  'rgba(219, 234, 254, 0.55)',
  'rgba(220, 252, 231, 0.55)',
  'rgba(254, 243, 199, 0.55)',
  'rgba(243, 232, 255, 0.55)',
  'rgba(252, 231, 243, 0.55)',
]

interface ChunkBlock {
  index: number
  html: string
  color: string
}

const chunkBlocks = computed(() => {
  return chunks.value.map((text, i) => ({
    index: i,
    html: DOMPurify.sanitize(marked.parse(text) as string),
    color: chunkColors[i % chunkColors.length],
  }))
})

// ---- 上传转换 ----
async function handleFileChange(uploadFile: any) {
  const file: File = uploadFile.raw
  converting.value = true
  const msg = ElMessage({ message: `正在转换 ${file.name}，请稍候…`, duration: 0, type: 'info' })
  try {
    const { data } = await convertFile(file)
    convertResult.value = data
    editableMarkdown.value = data.markdown
    resetChunkParams()
    previewVisible.value = true
  } catch (e: any) {
    ElMessage.error(extractApiError(e, '转换失败，请检查文件格式'))
  } finally {
    msg.close()
    converting.value = false
  }
}

// ---- 确认入库 ----
async function handleIngest() {
  if (!convertResult.value) return
  const err = validateSeparators(separatorsText.value)
  if (err) { separatorsError.value = err; return }
  ingesting.value = true
  try {
    const { data } = await ingestFile({
      file_name: convertResult.value.file_name,
      file_hash: convertResult.value.file_hash,
      file_size: convertResult.value.file_size,
      file_type: convertResult.value.file_type,
      markdown: editableMarkdown.value,
      chunk_size: chunkSize.value,
      chunk_overlap: chunkOverlap.value,
      chunk_separators: parseSeparators(separatorsText.value),
    })
    ElMessage.success('入库成功')
    previewVisible.value = false
    loadFiles()
  } catch (e: any) {
    ElMessage.error(extractApiError(e, '入库失败'))
  } finally {
    ingesting.value = false
  }
}

// ---- 文件列表 ----
async function loadFiles() {
  loadingList.value = true
  try {
    const { data } = await listFiles()
    fileList.value = data
  } finally {
    loadingList.value = false
  }
}

async function handleDelete(id: number) {
  try {
    await ElMessageBox.confirm('确认删除该文件？', '提示', { type: 'warning' })
    await deleteFile(id)
    ElMessage.success('删除成功')
    loadFiles()
  } catch (e: any) {
    if (e === 'cancel' || e?.message === 'cancel') return
    ElMessage.error(extractApiError(e, '删除失败'))
  }
}

// ---- 查看已入库分块 ----
const chunkViewVisible = ref(false)
const chunkViewFileName = ref('')
const chunkViewTotal = ref(0)
const chunkViewList = ref<string[]>([])
const chunkViewLoading = ref(false)

async function handleViewChunks(row: KnowledgeFileView) {
  chunkViewVisible.value = true
  chunkViewFileName.value = row.file_name
  chunkViewLoading.value = true
  try {
    const { data } = await getFileChunks(row.id)
    chunkViewList.value = data.chunks
    chunkViewTotal.value = data.total
  } catch {
    chunkViewList.value = []
    chunkViewTotal.value = 0
  } finally {
    chunkViewLoading.value = false
  }
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

onMounted(loadFiles)

onUnmounted(() => {
  if (_previewTimer) {
    clearTimeout(_previewTimer)
    _previewTimer = null
  }
  _previewId = -1
})
</script>

<template>
  <h2 class="page-title">知识库管理</h2>
  <p class="page-desc">支持文档上传、转换预览、确认入库。切分结果由后端实时计算，与入库完全一致。</p>

  <!-- 上传区域 -->
  <el-card style="margin-bottom: 16px">
    <el-upload
      drag
      action="#"
      :auto-upload="false"
      :on-change="handleFileChange"
      :show-file-list="false"
      :disabled="converting"
      accept=".pdf,.docx,.pptx,.xlsx,.md,.txt"
    >
      <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
      <div class="el-upload__text">拖拽文件到此处，或 <em>点击上传</em></div>
      <template #tip>
        <div class="el-upload__tip">支持 PDF / DOCX / PPTX / XLSX / MD / TXT，单文件不超过 20MB</div>
      </template>
    </el-upload>
  </el-card>

  <!-- 转换预览对话框 -->
  <el-dialog
    v-model="previewVisible"
    title="转换预览"
    width="90%"
    top="4vh"
    :close-on-click-modal="false"
  >
    <div class="preview-container">
      <!-- 左侧：编辑器 -->
      <div class="preview-pane">
        <div class="pane-header">Markdown 原文 <span class="hint">（可编辑）</span></div>
        <textarea
          v-model="editableMarkdown"
          class="md-editor"
          spellcheck="false"
          placeholder="加载中…"
        />
      </div>
      <!-- 右侧：chunk 预览 -->
      <div class="preview-pane">
        <div class="pane-header">
          分块预览
          <span class="hint">（仅供预览参考，实际入库结果以后端为准）</span>
          <span class="chunk-badge" v-if="totalChunks">{{ totalChunks }} chunks</span>
        </div>
        <div class="md-preview" v-loading="chunking">
          <div
            v-for="block in chunkBlocks"
            :key="block.index"
            class="chunk-block"
            :style="{ backgroundColor: block.color, borderLeftColor: block.color }"
          >
            <span class="chunk-index">#{{ block.index + 1 }}</span>
            <div class="chunk-html" v-html="block.html" />
          </div>
          <div v-if="!chunking && totalChunks === 0 && editableMarkdown" class="chunk-empty">
            暂未分块，输入内容后自动计算
          </div>
        </div>
      </div>
    </div>
    <!-- 切分参数 -->
    <div class="chunk-params">
      <div class="chunk-params-title">切分参数（留空则使用默认值）</div>
      <div class="chunk-params-row">
        <div class="chunk-param">
          <label>chunk_size <span class="default-hint">默认 {{ DEFAULT_CHUNK_SIZE }}</span></label>
          <el-input-number v-model="chunkSize" :min="100" :max="10000" :step="100" style="width: 100%" />
        </div>
        <div class="chunk-param">
          <label>chunk_overlap <span class="default-hint">默认 {{ DEFAULT_CHUNK_OVERLAP }}</span></label>
          <el-input-number v-model="chunkOverlap" :min="0" :max="2000" :step="50" style="width: 100%" />
        </div>
      </div>
      <div class="chunk-param" style="margin-top: 8px">
        <label>
          separators
          <span class="default-hint">英文逗号分隔，\n 表示换行，默认 {{ separatorsToDisplay(DEFAULT_SEPARATORS) }}</span>
        </label>
        <input
          v-model="separatorsText"
          class="separators-input"
          :class="{ 'separators-input--error': separatorsError }"
          spellcheck="false"
          placeholder="\n\n,\n, ,"
          @input="separatorsError = null"
        />
        <span v-if="separatorsError" class="separators-error">{{ separatorsError }}</span>
      </div>
    </div>
    <template #footer>
      <el-button @click="previewVisible = false">放弃</el-button>
      <el-button type="primary" :loading="ingesting" @click="handleIngest">确认入库</el-button>
    </template>
  </el-dialog>

  <!-- 文件列表 -->
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>已入库文件</span>
        <el-button size="small" @click="loadFiles">刷新</el-button>
      </div>
    </template>
    <el-table :data="fileList" v-loading="loadingList" empty-text="暂无文件">
      <el-table-column prop="file_name" label="文件名" min-width="200" show-overflow-tooltip />
      <el-table-column prop="file_type" label="类型" width="80" />
      <el-table-column label="大小" width="100">
        <template #default="{ row }">{{ formatSize(row.file_size) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.status === 'success' ? 'success' : 'danger'" size="small">
            {{ row.status === 'success' ? '成功' : '失败' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="total_chunks" label="块数" width="80" />
      <el-table-column label="入库时间" width="165">
        <template #default="{ row }">{{ row.created_at.slice(0, 19).replace('T', ' ') }}</template>
      </el-table-column>
      <el-table-column label="操作" width="130" fixed="right">
        <template #default="{ row }">
          <el-button type="primary" size="small" text @click="handleViewChunks(row)">查看</el-button>
          <el-button type="danger" size="small" text @click="handleDelete(row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>

  <!-- 已入库分块查看 -->
  <el-dialog
    v-model="chunkViewVisible"
    :title="`分块内容 — ${chunkViewFileName}`"
    width="70%"
    top="4vh"
  >
    <div class="chunk-view-body" v-loading="chunkViewLoading">
      <div class="chunk-view-header" v-if="chunkViewTotal">
        共 {{ chunkViewTotal }} 块
      </div>
      <div
        v-for="(text, i) in chunkViewList"
        :key="i"
        class="chunk-view-block"
        :style="{ backgroundColor: chunkColors[i % chunkColors.length] }"
      >
        <span class="chunk-index">#{{ i + 1 }}</span>
        <div class="chunk-html" v-html="DOMPurify.sanitize(marked.parse(text) as string)" />
      </div>
      <div v-if="!chunkViewLoading && chunkViewTotal === 0" class="chunk-empty">
        未查询到分块数据
      </div>
    </div>
  </el-dialog>
</template>

<style scoped>
/* ---- chunk params ---- */
.chunk-params {
  margin-top: 12px;
  padding: 12px;
  background: #fafafa;
  border: 1px solid #ebeef5;
  border-radius: 6px;
}

.chunk-params-title {
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 8px;
  color: #606266;
}

.chunk-params-row {
  display: flex;
  gap: 16px;
}

.chunk-param {
  flex: 1;
}

.chunk-param label {
  display: block;
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}

.default-hint {
  color: #c0c4cc;
  font-weight: 400;
}

.separators-input {
  width: 100%;
  padding: 8px 10px;
  font-size: 13px;
  font-family: 'Consolas', 'Courier New', monospace;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  outline: none;
  line-height: 1.6;
  box-sizing: border-box;
}

.separators-input:focus {
  border-color: #409eff;
}

.separators-input--error {
  border-color: #f56c6c;
}

.separators-input--error:focus {
  border-color: #f56c6c;
  box-shadow: 0 0 0 2px rgba(245, 108, 108, 0.2);
}

.separators-error {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  color: #f56c6c;
}

/* ---- preview panes ---- */
.preview-container {
  display: flex;
  gap: 16px;
  height: 62vh;
}

.preview-pane {
  flex: 1;
  display: flex;
  flex-direction: column;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  overflow: hidden;
}

.pane-header {
  padding: 8px 14px;
  background: #f5f7fa;
  font-size: 13px;
  font-weight: 500;
  border-bottom: 1px solid #e4e7ed;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}

.hint {
  font-weight: 400;
  color: #909399;
}

.chunk-badge {
  margin-left: auto;
  font-size: 11px;
  font-weight: 500;
  background: #409eff;
  color: #fff;
  padding: 1px 8px;
  border-radius: 10px;
}

/* ---- editor ---- */
.md-editor {
  flex: 1;
  padding: 12px;
  font-size: 13px;
  font-family: 'Consolas', 'Courier New', monospace;
  line-height: 1.7;
  border: none;
  outline: none;
  resize: none;
  overflow-y: auto;
  color: #333;
  background: #fff;
}

/* ---- chunk preview ---- */
.md-preview {
  flex: 1;
  padding: 10px;
  overflow-y: auto;
  font-size: 14px;
  line-height: 1.8;
}

.chunk-block {
  margin-bottom: 8px;
  padding: 8px 12px;
  border-radius: 4px;
  border-left: 3px solid;
}

.chunk-index {
  display: inline-block;
  font-size: 10px;
  font-weight: 600;
  color: #909399;
  user-select: none;
  margin-bottom: 4px;
}

.chunk-html {
  display: block;
}

.chunk-empty {
  text-align: center;
  color: #c0c4cc;
  padding: 40px 0;
  font-size: 13px;
}

.chunk-html :deep(p) {
  margin: 4px 0;
}

.chunk-html :deep(h1),
.chunk-html :deep(h2),
.chunk-html :deep(h3),
.chunk-html :deep(h4) {
  margin: 8px 0 4px;
  line-height: 1.4;
}

.md-preview :deep(h1),
.md-preview :deep(h2),
.md-preview :deep(h3),
.md-preview :deep(h4) {
  margin: 14px 0 6px;
  line-height: 1.4;
}

.md-preview :deep(p) {
  margin: 6px 0;
}

.md-preview :deep(ul),
.md-preview :deep(ol) {
  padding-left: 20px;
  margin: 6px 0;
}

.md-preview :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 10px 0;
  font-size: 13px;
}

.md-preview :deep(th),
.md-preview :deep(td) {
  border: 1px solid #dcdfe6;
  padding: 6px 10px;
  text-align: left;
}

.md-preview :deep(th) {
  background: #f5f7fa;
  font-weight: 600;
}

.md-preview :deep(code) {
  background: #f0f2f5;
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 12px;
  font-family: 'Consolas', monospace;
}

.md-preview :deep(pre) {
  background: #f5f7fa;
  padding: 12px;
  border-radius: 6px;
  overflow-x: auto;
  margin: 8px 0;
}

.md-preview :deep(pre code) {
  background: none;
  padding: 0;
}

.md-preview :deep(blockquote) {
  border-left: 3px solid #dcdfe6;
  margin: 8px 0;
  padding: 4px 12px;
  color: #909399;
}

.md-preview :deep(hr) {
  border: none;
  border-top: 1px solid #e4e7ed;
  margin: 12px 0;
}

/* ---- chunk viewer dialog ---- */
.chunk-view-body {
  max-height: 65vh;
  overflow-y: auto;
}

.chunk-view-header {
  margin-bottom: 10px;
  font-size: 13px;
  color: #909399;
}

.chunk-view-block {
  margin-bottom: 8px;
  padding: 10px 14px;
  border-radius: 4px;
}
</style>
