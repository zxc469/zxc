<script setup lang="ts">
import { ref, reactive, watch, computed, onMounted, onUnmounted } from 'vue'
import {
  Search,
  ChatDotRound,
  User,
  Service,
  Plus
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useWebSocket } from '@/composables/useWebSocket'
import { useAppStore } from '@/stores/app'
import {
  getUserSessions,
  createSession as createSessionApi,
  getSessionDetail,
  getSessionMessages,
  sendSessionMessage,
  chatWithAIStream,
  markSessionRead,
  transferToAgent,
  type SessionListItem,
  type SessionDetail,
  type MessageItem,
  type SSEEvent
} from '@/api/sessions'

// ==================== 左侧：会话历史 ====================
const chatSessions = ref<SessionListItem[]>([])
const activeSession = ref<number | null>(null)
const searchQuery = ref('')
const sessionsLoading = ref(false)

// 分页参数
const sessionPage = ref(1)
const sessionPageSize = ref(20)
const hasMoreSessions = ref(true)

// 加载会话列表
async function loadSessions(isLoadMore = false) {
  if (sessionsLoading.value) return
  sessionsLoading.value = true
  
  try {
    const page = isLoadMore ? sessionPage.value + 1 : 1
    const res = await getUserSessions({
      page,
      page_size: sessionPageSize.value,
      status: undefined // 可添加状态过滤
    })
    
    if (isLoadMore) {
      chatSessions.value.push(...res.data.items)
    } else {
      chatSessions.value = res.data.items
    }
    
    sessionPage.value = page
    hasMoreSessions.value = res.data.items.length >= sessionPageSize.value
  } catch (err) {
    ElMessage.error('加载会话失败')
  } finally {
    sessionsLoading.value = false
  }
}

// 会话列表滚动加载
function onSessionListScroll(event: Event) {
  const target = event.target as HTMLElement
  if (!target) return
  
  const isNearBottom = target.scrollHeight - target.scrollTop - target.clientHeight < 50
  if (isNearBottom && hasMoreSessions.value && !sessionsLoading.value) {
    loadSessions(true)
  }
}

function selectSession(id: number) {
  if (activeSession.value === id) return
  // 切换会话时停止当前生成
  if (isGenerating.value) {
    stopGeneration()
  }
  activeSession.value = id
  inputMessage.value = ''
  // 滚动到底部由 watch 处理
}

async function createSession() {
  try {
    const res = await createSessionApi({ source: 'user_initiated' })
    // 新会话添加到列表顶部
    chatSessions.value.unshift(res.data)
    // 自动选中新会话
    selectSession(res.data.id)
    ElMessage.success('会话创建成功')
  } catch (err) {
    ElMessage.error('创建会话失败')
  }
}

// ==================== 中间：聊天窗口 ====================
// 会话详情（点击后才加载）
const sessionDetail = ref<SessionDetail | null>(null)
const sessionDetailLoading = ref(false)

// 消息列表
const messages = ref<MessageItem[]>([])
const messagesLoading = ref(false)
const hasMoreMessages = ref(true)
const messagePage = ref(1)
const messagePageSize = ref(30)

const inputMessage = ref('')
const sessionMode = computed(() => {
  return sessionDetail.value?.handled_by === 'agent' ? 'agent' : 'ai'
})
const isClosed = computed(() => sessionDetail.value?.status === 'closed')
const messagesContainer = ref<HTMLElement | null>(null)

// SSE 流式生成状态
const isGenerating = ref(false)
const abortStreamFn = ref<(() => void) | null>(null)
const currentToolStatus = ref('')
const pendingAiMessageId = ref<number | null>(null)  // 正在生成中的 AI 消息临时 ID

// 加载会话详情
async function loadSessionDetail(sessionId: number) {
  if (sessionDetailLoading.value) return
  sessionDetailLoading.value = true
  
  try {
    const res = await getSessionDetail(sessionId)
    sessionDetail.value = res.data
  } catch (err) {
    ElMessage.error('加载会话详情失败')
  } finally {
    sessionDetailLoading.value = false
  }
}

// 加载消息列表
async function loadMessages(sessionId: number, isLoadMore = false) {
  if (messagesLoading.value) return
  messagesLoading.value = true
  
  try {
    const page = isLoadMore ? messagePage.value + 1 : 1
    const res = await getSessionMessages(sessionId, {
      page,
      page_size: messagePageSize.value,
      before_id: isLoadMore ? messages.value[0]?.id : undefined
    })
    
    if (isLoadMore) {
      // 加载更多消息（早期消息）插入到列表前面
      messages.value.unshift(...res.data.items)
    } else {
      messages.value = res.data.items
    }
    
    messagePage.value = page
    hasMoreMessages.value = res.data.has_more
    
    // 标记已读
    await markSessionRead(sessionId)
    // 同步侧边栏未读计数
    const sess = chatSessions.value.find(s => s.id === sessionId)
    if (sess) sess.unread_count = 0

    // 滚动处理
    if (!isLoadMore) {
      scrollToBottom()
    }
  } catch (err) {
    ElMessage.error('加载消息失败')
  } finally {
    messagesLoading.value = false
  }
}

// 消息列表滚动加载（向上滚动加载历史消息）
function onMessagesScroll(event: Event) {
  const target = event.target as HTMLElement
  if (!target || target.scrollTop > 50) return
  
  // 滚动到顶部时加载更多
  if (hasMoreMessages.value && !messagesLoading.value) {
    const oldHeight = target.scrollHeight
    
    loadMessages(activeSession.value!, true).then(() => {
      // 保持滚动位置
      setTimeout(() => {
        target.scrollTop = target.scrollHeight - oldHeight
      }, 50)
    })
  }
}

// 监听会话切换
watch(activeSession, async (newId) => {
  if (!newId) {
    sessionDetail.value = null
    messages.value = []
    return
  }

  // 重置分页
  messagePage.value = 1
  hasMoreMessages.value = true

  // 并行加载详情和消息
  await Promise.all([
    loadSessionDetail(newId),
    loadMessages(newId)
  ])

  scrollToBottom()
})

// ==================== WebSocket (会话级 + 全局) ====================
const appStore = useAppStore()
const { status: wsStatus, lastMessage, connect, disconnect, send } = useWebSocket()

// 全局通知 WebSocket：接收跨会话的未读消息增量
const {
  status: globalWsStatus,
  lastMessage: globalLastMessage,
  connect: connectGlobal,
  disconnect: disconnectGlobal,
} = useWebSocket()

const wsStatusText = computed(() => {
  switch (wsStatus.value) {
    case 'connecting': return '连接中...'
    case 'connected': return '在线'
    case 'error': return '连接异常'
    default: return '已断开'
  }
})

const wsStatusColor = computed(() => {
  switch (wsStatus.value) {
    case 'connected': return '#67c23a'
    case 'connecting': return '#e6a23c'
    default: return '#f56c6c'
  }
})

function _wsBaseUrl(path: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/api/v1${path}`
}

/** 构建传递 token 的子协议列表 */
function _tokenProtocols(): string[] {
  return appStore.accessToken ? [`access_token.${appStore.accessToken}`] : []
}

function buildWsUrl(sessionId: number) {
  return _wsBaseUrl(`/ws/chat/${sessionId}`)
}

function buildGlobalWsUrl() {
  return _wsBaseUrl('/ws/user/notifications')
}

// 只在选中会话时建立 per-session WebSocket 连接
watch(activeSession, (newId) => {
  if (newId) {
    connect(buildWsUrl(newId), _tokenProtocols())
  } else {
    disconnect()
  }
})

// 全局通知：实时更新侧边栏未读计数
watch(globalLastMessage, (msg: any) => {
  if (!msg || msg.type !== 'new_message') return

  const sid = msg.session_id as number
  if (!sid) return

  // 正在查看该会话时不累加未读数
  if (activeSession.value === sid) return

  const session = chatSessions.value.find(s => s.id === sid)
  if (session) {
    session.unread_count = (session.unread_count || 0) + 1
    session.last_message = msg.content || ''
    session.last_message_time = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }
})

// 接收 WebSocket 消息
watch(lastMessage, (msg) => {
  if (!msg || !activeSession.value) return

  // system 消息仅做前端提示，不进入消息列表
  if (msg.type === 'system') {
    ElMessage.success(msg.content)
    // 会话关闭通知：同步状态
    if (msg.event === 'session_closed' && sessionDetail.value) {
      sessionDetail.value.status = 'closed'
      const s = chatSessions.value.find(s => s.id === activeSession.value)
      if (s) s.status = 'closed'
    }
    // 模式切换通知：同步 handled_by
    if (msg.event === 'handled_by_changed' && sessionDetail.value) {
      sessionDetail.value.handled_by = msg.handled_by
    }
    return
  }

  // 客服主动发消息时，如果当前是 AI 模式则自动切换
  if (msg.sender === 'agent' && sessionMode.value === 'ai') {
    if (sessionDetail.value) {
      sessionDetail.value.handled_by = 'agent'
    }
    ElMessage.info('人工客服已接入')
  }

  // 转换为 MessageItem 格式
  const newMsg: MessageItem = {
    id: Number(msg.id) || Date.now(),
    session_id: activeSession.value,
    sender_type: msg.sender as MessageItem['sender_type'],
    sender_id: null,
    message_type: 'text',
    content: msg.content,
    is_read: true,
    read_at: new Date().toISOString(),
    created_at: new Date().toISOString(),
    metadata: null
  }

  messages.value.push(newMsg)
  scrollToBottom()

  // 更新会话列表的最后消息
  const session = chatSessions.value.find(s => s.id === activeSession.value)
  if (session) {
    session.last_message = msg.content
    session.last_message_time = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }
})

function sendMessage() {
  if (!inputMessage.value.trim() || !activeSession.value || isClosed.value) return
  if (isGenerating.value) return  // 生成中不允许发送新消息

  const userContent = inputMessage.value
  const sid = activeSession.value

  if (sessionMode.value === 'ai') {
    // AI 模式：乐观插入用户消息，然后通过 SSE 流式获取 AI 回复
    const tempUserMsgId = Date.now()
    const tempUserMsg: MessageItem = {
      id: tempUserMsgId,
      session_id: sid,
      sender_type: 'user',
      sender_id: null,
      message_type: 'text',
      content: userContent,
      is_read: true,
      read_at: new Date().toISOString(),
      created_at: new Date().toISOString(),
      metadata: null
    }
    messages.value.push(tempUserMsg)
    inputMessage.value = ''
    scrollToBottom()

    // 创建占位 AI 消息气泡
    const tempAiMsgId = Date.now() + 1
    pendingAiMessageId.value = tempAiMsgId
    const tempAiMsg: MessageItem = {
      id: tempAiMsgId,
      session_id: sid,
      sender_type: 'ai',
      sender_id: null,
      message_type: 'text',
      content: '',
      is_read: true,
      read_at: new Date().toISOString(),
      created_at: new Date().toISOString(),
      metadata: null
    }
    messages.value.push(tempAiMsg)
    scrollToBottom()

    isGenerating.value = true
    currentToolStatus.value = ''

    const abort = chatWithAIStream(sid, {
      message_type: 'text',
      content: userContent
    }, (event: SSEEvent) => {
      switch (event.event) {
        case 'message_start': {
          // 每次 agent_llm_node 启动时清空占位内容
          // （处理审核修正导致 agent 重新生成的情形，避免旧草稿残留）
          const msg = messages.value.find(m => m.id === tempAiMsgId)
          if (msg) {
            msg.content = ''
          }
          currentToolStatus.value = ''
          break
        }
        case 'token': {
          const data = event.data as { token: string; seq: number }
          const msg = messages.value.find(m => m.id === tempAiMsgId)
          if (msg) {
            msg.content += data.token
          }
          scrollToBottom()
          break
        }
        case 'tool_call': {
          const data = event.data as { tool_name: string; status: string }
          if (data.status === 'running') {
            currentToolStatus.value = `正在查询知识库…`
          } else {
            currentToolStatus.value = ''
          }
          break
        }
        case 'done': {
          const data = event.data as { answer: string; need_human: boolean }
          isGenerating.value = false
          abortStreamFn.value = null
          pendingAiMessageId.value = null
          currentToolStatus.value = ''

          // 确保 AI 消息内容完整
          const msg = messages.value.find(m => m.id === tempAiMsgId)
          if (msg && !msg.content) {
            msg.content = data.answer || '请稍后重试'
          }

          // 更新会话列表
          const session = chatSessions.value.find(s => s.id === sid)
          if (session) {
            const finalContent = messages.value.find(m => m.id === tempAiMsgId)?.content || ''
            session.last_message = finalContent
            session.last_message_time = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
          }

          // AI 判定需转人工
          if (data.need_human) {
            if (sessionDetail.value) {
              sessionDetail.value.handled_by = 'agent'
            }
            ElMessage.warning('AI 无法处理您的问题，已转接人工客服，请稍候')
          }
          break
        }
        case 'error': {
          const data = event.data as { message: string }
          isGenerating.value = false
          abortStreamFn.value = null
          pendingAiMessageId.value = null
          currentToolStatus.value = ''

          // 标记 AI 消息为错误
          const msg = messages.value.find(m => m.id === tempAiMsgId)
          if (msg) {
            msg.content = msg.content || '抱歉，服务暂时不可用，请稍后重试。'
          }
          ElMessage.error('AI 回复失败')
          break
        }
      }
    })

    abortStreamFn.value = abort
  } else {
    // Agent 模式：走人工客服
    sendMessageToAgent(sid, userContent)
  }
}

async function sendMessageToAgent(sid: number, userContent: string) {
  try {
    const res = await sendSessionMessage(sid, {
      message_type: 'text',
      content: userContent
    })

    messages.value.push(res.data)
    inputMessage.value = ''
    scrollToBottom()

    const session = chatSessions.value.find(s => s.id === sid)
    if (session) {
      session.last_message = res.data.content
      session.last_message_time = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    }
  } catch (err) {
    ElMessage.error('发送消息失败')
  }
}

/** 停止当前 AI 生成 */
function stopGeneration() {
  if (abortStreamFn.value) {
    abortStreamFn.value()
    abortStreamFn.value = null
  }
  isGenerating.value = false
  currentToolStatus.value = ''

  if (pendingAiMessageId.value) {
    const msg = messages.value.find(m => m.id === pendingAiMessageId.value)
    if (msg && !msg.content) {
      msg.content = '[已停止生成]'
    } else if (msg) {
      msg.content += '\n\n[已停止生成]'
    }
    pendingAiMessageId.value = null
  }
}

async function handleTransferToAgent() {
  if (!activeSession.value) return

  try {
    await transferToAgent(activeSession.value)
    ElMessage.success('已转接人工客服，请稍候')

    const session = chatSessions.value.find(s => s.id === activeSession.value)
    if (session) {
      session.status = 'waiting'
    }
    if (sessionDetail.value) {
      sessionDetail.value.status = 'waiting'
      sessionDetail.value.handled_by = 'agent'
    }
  } catch (err) {
    ElMessage.error('转人工失败')
  }
}

function scrollToBottom() {
  setTimeout(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  }, 100)
}

// 格式化时间
function formatTime(timeStr: string) {
  const date = new Date(timeStr)
  const now = new Date()
  const isToday = date.toDateString() === now.toDateString()
  
  if (isToday) {
    return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  } else {
    return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
  }
}

// 组件挂载时加载会话列表 + 建立全局通知 WS
onMounted(() => {
  loadSessions()
  connectGlobal(buildGlobalWsUrl(), _tokenProtocols())
})

// 组件卸载时断开 WebSocket 并停止生成
onUnmounted(() => {
  if (isGenerating.value) {
    stopGeneration()
  }
  disconnect()
  disconnectGlobal()
})
</script>

<template>
  <div class="user-chat-container">
    <!-- ==================== 左侧：会话历史 ==================== -->
    <div class="chat-history">
      <div class="history-header">
        <h3>会话历史</h3>
        <el-input
          v-model="searchQuery"
          :prefix-icon="Search"
          placeholder="搜索会话"
          size="small"
          clearable
        />
      </div>

      <div class="history-new">
        <el-button type="primary" :icon="Plus" class="new-session-btn" @click="createSession">
          新建会话
        </el-button>
      </div>

      <div class="history-body">
        <div
          v-for="session in chatSessions"
          :key="session.id"
          class="session-item"
          :class="{ active: activeSession === session.id }"
          @click="selectSession(session.id)"
        >
          <div class="session-info">
            <div class="session-top">
              <span class="title">{{ session.session_no }}</span>
              <span class="time">{{ formatTime(session.created_at) }}</span>
            </div>
            <div class="session-bottom">
              <span class="last-message">{{ session.last_message || '暂无消息' }}</span>
            </div>
          </div>

          <el-badge
            v-if="session.unread_count && session.unread_count > 0"
            :value="session.unread_count"
            class="unread-badge"
          />
        </div>
      </div>
    </div>

    <!-- ==================== 中间：聊天窗口 ==================== -->
    <div v-if="activeSession" class="chat-window">
      <!-- 聊天头部 -->
      <div class="chat-header">
        <div class="header-left">
          <el-avatar :size="36">
            <el-icon><Service /></el-icon>
          </el-avatar>
          <div class="chat-info">
            <h4>
              智能客服助手
              <el-tag v-if="isClosed" size="small" type="info" effect="plain">已结束</el-tag>
              <el-tag v-else-if="sessionMode === 'ai'" size="small" type="success" effect="plain">AI 模式</el-tag>
              <el-tag v-else size="small" type="warning" effect="plain">人工模式</el-tag>
            </h4>
            <span class="status-text" :style="{ color: wsStatusColor }">{{ wsStatusText }}</span>
          </div>
        </div>
        <div class="header-actions" v-if="sessionMode === 'ai' && !isClosed">
          <el-button :icon="Service" size="small" type="warning" @click="handleTransferToAgent">转人工</el-button>
        </div>
      </div>

      <!-- 聊天消息区 -->
      <div class="chat-messages" ref="messagesContainer" @scroll="onMessagesScroll">
        <div
          v-for="msg in messages"
          :key="msg.id"
          class="message-item"
          :class="msg.sender_type"
        >
          <el-avatar :size="32" class="message-avatar">
            <el-icon>
              <User v-if="msg.sender_type === 'user'" />
              <Service v-else />
            </el-icon>
          </el-avatar>

          <div class="message-content">
            <div class="message-bubble">
              {{ msg.content }}
              <span
                v-if="msg.id === pendingAiMessageId && currentToolStatus"
                class="tool-status"
              >{{ currentToolStatus }}</span>
              <span
                v-if="msg.id === pendingAiMessageId && isGenerating && !currentToolStatus && !msg.content"
                class="typing-indicator"
              ><span class="dot"></span><span class="dot"></span><span class="dot"></span></span>
            </div>
            <div class="message-time">{{ formatTime(msg.created_at) }}</div>
          </div>
        </div>
      </div>

      <!-- 输入区域 -->
      <div class="chat-input-area">
        <div class="input-toolbar">
          <el-button size="small" plain>常见问题</el-button>
          <el-button size="small" plain>表情</el-button>
          <el-button size="small" plain>图片</el-button>
        </div>

        <el-input
          v-model="inputMessage"
          type="textarea"
          :rows="3"
          :placeholder="isClosed ? '会话已结束' : isGenerating ? 'AI 正在生成回复…' : '输入您的问题... (Ctrl+Enter 发送)'"
          resize="none"
          :disabled="isClosed"
          @keydown.ctrl.enter="sendMessage"
        />

        <div class="input-actions">
          <el-button
            v-if="isGenerating"
            type="danger"
            @click="stopGeneration"
          >
            停止生成
          </el-button>
          <el-button
            v-else
            @click="sendMessage"
            type="primary"
            :disabled="isClosed"
          >
            <el-icon><ChatDotRound /></el-icon>
            发送
          </el-button>
        </div>
      </div>
    </div>
    <div v-else class="chat-empty">
      <el-empty description="请从左侧选择或新建一个会话" />
    </div>
  </div>
</template>

<style scoped>
.user-chat-container {
  display: flex;
  height: calc(100vh - 120px);
  background: #f5f7fa;
  gap: 1px;
}

/* ==================== 左侧：会话历史 ==================== */
.chat-history {
  width: 280px;
  background: #fff;
  display: flex;
  flex-direction: column;
}

.history-header {
  padding: 16px;
  border-bottom: 1px solid #e4e7ed;
}

.history-header h3 {
  margin: 0 0 12px 0;
  font-size: 16px;
  font-weight: 600;
}

.history-body {
  flex: 1;
  overflow-y: auto;
}

.history-new {
  padding: 10px 16px;
  border-bottom: 1px solid #e4e7ed;
}

.new-session-btn {
  width: 100%;
}

.session-item {
  padding: 12px 16px;
  cursor: pointer;
  transition: background 0.2s;
  border-bottom: 1px solid #f0f0f0;
  position: relative;
}

.session-item:hover {
  background: #f5f7fa;
}

.session-item.active {
  background: #ecf5ff;
}

.session-info {
  flex: 1;
}

.session-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.session-top .title {
  font-weight: 500;
  font-size: 14px;
}

.session-top .time {
  font-size: 12px;
  color: #909399;
}

.session-bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.last-message {
  font-size: 13px;
  color: #606266;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
}

.unread-badge {
  position: absolute;
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
}

/* ==================== 中间：聊天窗口 ==================== */
.chat-window {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #fff;
}

.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid #e4e7ed;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.chat-info h4 {
  margin: 0;
  font-size: 16px;
}

.status-text {
  font-size: 12px;
  transition: color 0.3s;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  background: #f5f7fa;
}

.message-item {
  display: flex;
  margin-bottom: 16px;
  gap: 8px;
}

/* 用户自己的消息显示在右侧 */
.message-item.user {
  flex-direction: row-reverse;
}

/* 客服/AI的消息显示在左侧 */
.message-item.ai,
.message-item.agent {
  flex-direction: row;
}

.message-content {
  max-width: 60%;
}

.message-bubble {
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.5;
  word-wrap: break-word;
}

/* 用户消息样式 - 蓝色背景 */
.message-item.user .message-bubble {
  background: #409eff;
  color: #fff;
}

/* 客服/AI消息样式 - 白色背景 */
.message-item.ai .message-bubble,
.message-item.agent .message-bubble {
  background: #fff;
  border: 1px solid #e4e7ed;
}

.message-time {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
  padding: 0 4px;
}

/* 用户消息时间右对齐 */
.message-item.user .message-time {
  text-align: right;
}

.chat-input-area {
  border-top: 1px solid #e4e7ed;
  padding: 16px;
  background: #fff;
}

.input-toolbar {
  margin-bottom: 12px;
  display: flex;
  gap: 8px;
}

.input-actions {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}

.chat-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fff;
}

/* 打字指示器 */
.typing-indicator {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  margin-left: 4px;
}

.typing-indicator .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #909399;
  animation: typing-bounce 1.4s infinite ease-in-out both;
}

.typing-indicator .dot:nth-child(1) { animation-delay: 0s; }
.typing-indicator .dot:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator .dot:nth-child(3) { animation-delay: 0.4s; }

@keyframes typing-bounce {
  0%, 80%, 100% {
    transform: scale(0.6);
    opacity: 0.4;
  }
  40% {
    transform: scale(1);
    opacity: 1;
  }
}

/* 工具调用状态 */
.tool-status {
  display: block;
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
  font-style: italic;
}

/* 滚动条样式 */
::-webkit-scrollbar {
  width: 6px;
}

::-webkit-scrollbar-thumb {
  background: #dcdfe6;
  border-radius: 3px;
}

::-webkit-scrollbar-thumb:hover {
  background: #c0c4cc;
}
</style>
