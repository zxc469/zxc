<script setup lang="ts">
import { ref, watch, computed, onMounted, onUnmounted } from 'vue'
import {
  Search,
  ChatDotRound,
  User,
  Service
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useWebSocket } from '@/composables/useWebSocket'
import { useAppStore } from '@/stores/app'
import {
  getAgentSessions,
  getAgentSessionDetail,
  getAgentSessionMessages,
  sendAgentMessage,
  acceptSession,
  markAgentSessionRead,
  getAgentSessionStats,
  transferToAI,
  type SessionListItem,
  type SessionDetail,
  type MessageItem
} from '@/api/sessions'

// ==================== 左侧：会话列表 ====================
const conversations = ref<SessionListItem[]>([])
const activeConversation = ref<number | null>(null)
const searchQuery = ref('')
const sessionsLoading = ref(false)

// 分页参数
const sessionPage = ref(1)
const sessionPageSize = ref(20)
const hasMoreSessions = ref(true)

// 会话统计
const stats = ref({
  active_count: 0,
  waiting_count: 0,
  closed_count: 0
})

// 加载会话列表
async function loadSessions(isLoadMore = false) {
  if (sessionsLoading.value) return
  sessionsLoading.value = true
  
  try {
    const page = isLoadMore ? sessionPage.value + 1 : 1
    const res = await getAgentSessions({
      page,
      page_size: sessionPageSize.value
    })
    
    if (isLoadMore) {
      conversations.value.push(...res.data.items)
    } else {
      conversations.value = res.data.items
    }
    
    sessionPage.value = page
    hasMoreSessions.value = res.data.items.length >= sessionPageSize.value
  } catch (err) {
    ElMessage.error('加载会话失败')
  } finally {
    sessionsLoading.value = false
  }
}

// 加载统计数据
async function loadStats() {
  try {
    const res = await getAgentSessionStats()
    stats.value = {
      active_count: res.data.active_sessions,
      waiting_count: res.data.waiting_sessions,
      closed_count: res.data.closed_today
    }
  } catch (err) {
    // 统计加载失败不影响主流程
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

function selectConversation(id: number) {
  if (activeConversation.value === id) return
  activeConversation.value = id
  inputMessage.value = ''
}

// ==================== 中间：会话详情 ====================
const sessionDetail = ref<SessionDetail | null>(null)
const sessionDetailLoading = ref(false)

const messages = ref<MessageItem[]>([])
const messagesLoading = ref(false)
const hasMoreMessages = ref(true)
const messagePage = ref(1)
const messagePageSize = ref(30)

// 消息缓存：WS 推送新会话时预存消息，点击后直接取出，无需调 API
const messageCache = new Map<number, MessageItem[]>()

const inputMessage = ref('')
const messagesContainer = ref<HTMLElement | null>(null)

// 加载会话详情
async function loadSessionDetail(sessionId: number) {
  if (sessionDetailLoading.value) return
  sessionDetailLoading.value = true
  
  try {
    const res = await getAgentSessionDetail(sessionId)
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
    const res = await getAgentSessionMessages(sessionId, {
      page,
      page_size: messagePageSize.value,
      before_id: isLoadMore ? messages.value[0]?.id : undefined
    })
    
    if (isLoadMore) {
      messages.value.unshift(...res.data.items)
    } else {
      messages.value = res.data.items
    }
    
    messagePage.value = page
    hasMoreMessages.value = res.data.has_more
    
    // 标记已读
    await markAgentSessionRead(sessionId)
    // 同步侧边栏未读计数
    const conv = conversations.value.find(c => c.id === sessionId)
    if (conv) conv.unread_count = 0

    if (!isLoadMore) {
      scrollToBottom()
    }
  } catch (err) {
    ElMessage.error('加载消息失败')
  } finally {
    messagesLoading.value = false
  }
}

// 消息列表滚动加载
function onMessagesScroll(event: Event) {
  const target = event.target as HTMLElement
  if (!target || target.scrollTop > 50) return
  
  if (hasMoreMessages.value && !messagesLoading.value) {
    const oldHeight = target.scrollHeight
    
    loadMessages(activeConversation.value!, true).then(() => {
      setTimeout(() => {
        target.scrollTop = target.scrollHeight - oldHeight
      }, 50)
    })
  }
}

// 监听会话切换
watch(activeConversation, async (newId) => {
  if (!newId) {
    sessionDetail.value = null
    messages.value = []
    return
  }

  messagePage.value = 1
  hasMoreMessages.value = true

  // 消息优先从 WS 推送的缓存中取，没有缓存再调 API
  const cached = messageCache.get(newId)
  if (cached) {
    messageCache.delete(newId)
    messages.value = cached
    await loadSessionDetail(newId)
    markAgentSessionRead(newId)
    const c = conversations.value.find(c => c.id === newId)
    if (c) c.unread_count = 0
    scrollToBottom()
  } else {
    await Promise.all([
      loadSessionDetail(newId),
      loadMessages(newId)
    ])
  }

  // 点击查看 waiting 会话时自动接受
  if (sessionDetail.value?.status === 'waiting') {
    try {
      await acceptSession(newId)
      sessionDetail.value.status = 'active'
      const conv = conversations.value.find(c => c.id === newId)
      if (conv) conv.status = 'active'
      loadStats()
    } catch (_) {
      // 接受失败不阻塞查看
    }
  }

  scrollToBottom()
})

// ==================== WebSocket ====================
const appStore = useAppStore()
const { status: wsStatus, lastMessage, connect, disconnect, send } = useWebSocket()

// 客服全局通知 WebSocket：接收新会话分配通知
const {
  status: notifyWsStatus,
  lastMessage: lastNotifyMsg,
  connect: connectNotify,
  disconnect: disconnectNotify,
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

/** 获取最新的 access token（直接读 localStorage，避免 Axios 续期后 store 未同步） */
function _latestToken(): string {
  return localStorage.getItem('app_access_token') || appStore.accessToken
}

function _tokenProtocols(): string[] {
  const t = _latestToken()
  return t ? [`access_token.${t}`] : []
}

function buildWsUrl(sessionId: number) {
  return _wsBaseUrl(`/ws/agent/${sessionId}`)
}

// 只在选中会话时建立 WebSocket 连接
watch(activeConversation, (newId) => {
  if (newId) {
    connect(buildWsUrl(newId), _tokenProtocols())
  } else {
    disconnect()
  }
})

// 接收 WebSocket 消息
watch(lastMessage, (msg) => {
  if (!msg || !activeConversation.value) return

  // system 消息仅做前端提示，不进入消息列表
  if (msg.type === 'system') {
    ElMessage.success(msg.content)
    if (msg.event === 'session_closed' && sessionDetail.value) {
      sessionDetail.value.status = 'closed'
      const c = conversations.value.find(c => c.id === activeConversation.value)
      if (c) c.status = 'closed'
    }
    // 模式切换通知：同步 handled_by
    if (msg.event === 'handled_by_changed' && sessionDetail.value) {
      sessionDetail.value.handled_by = msg.handled_by
      const c = conversations.value.find(c => c.id === activeConversation.value)
      if (c) c.handled_by = msg.handled_by
    }
    return
  }

  const newMsg: MessageItem = {
    id: Number(msg.id) || Date.now(),
    session_id: activeConversation.value,
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

  // 更新会话列表
  const conv = conversations.value.find(c => c.id === activeConversation.value)
  if (conv) {
    conv.last_message = msg.content
    conv.last_message_time = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }
})

// 全局通知 WebSocket：接收新会话分配事件，自动刷新侧边栏列表
// 注意：token 直接从 localStorage 取最新值，因为 Axios 拦截器续期时只写
// localStorage 不更新 Pinia store，用 store 拿到的可能是过期的旧 token
function buildNotifyWsUrl() {
  return _wsBaseUrl('/ws/agent/notifications')
}

watch(lastNotifyMsg, (msg) => {
  if (!msg || msg.type !== 'system') return
  if (msg.event === 'new_session_assigned' || msg.event === 'new_waiting_session') {
    // 后端推送了完整会话上下文，直接插入侧边栏顶部，无需 HTTP 请求
    const session = (msg as any).session as SessionListItem | undefined
    const msgs = (msg as any).messages as MessageItem[] | undefined

    if (session) {
      // 避免重复插入
      if (!conversations.value.find(c => c.id === session.id)) {
        conversations.value.unshift(session)
        ElMessage.info(msg.content)
      }
    }
    if (msgs && session) {
      messageCache.set(session.id, msgs)
    }
    loadStats()
  }
})

async function handleTransferToAI() {
  if (!activeConversation.value) return

  const sid = activeConversation.value

  try {
    await transferToAI(sid)
    ElMessage.success('已转AI模式')

    // 转AI后该会话不再属于当前客服，从列表中移除
    conversations.value = conversations.value.filter(c => c.id !== sid)
    sessionDetail.value = null
    messages.value = []
    activeConversation.value = null
    disconnect()
    loadStats()
  } catch (err) {
    ElMessage.error('转AI失败')
  }
}

async function sendMessage() {
  if (!inputMessage.value.trim() || !activeConversation.value) return

  try {
    const res = await sendAgentMessage(activeConversation.value, {
      message_type: 'text',
      content: inputMessage.value
    })

    messages.value.push(res.data)
    inputMessage.value = ''
    scrollToBottom()

    // 更新会话列表
    const conv = conversations.value.find(c => c.id === activeConversation.value)
    if (conv) {
      conv.last_message = res.data.content
      conv.last_message_time = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    }
  } catch (err) {
    ElMessage.error('发送消息失败')
  }
}

const sessionMode = computed(() => {
  return sessionDetail.value?.handled_by === 'agent' ? 'agent' : 'ai'
})

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

// 组件挂载时加载数据，建立全局通知 WebSocket
onMounted(() => {
  loadSessions()
  loadStats()
  connectNotify(buildNotifyWsUrl(), _tokenProtocols())
})

// 组件卸载时断开所有 WebSocket
onUnmounted(() => {
  disconnect()
  disconnectNotify()
})
</script>

<template>
  <div class="agent-conversations-container">
    <!-- ==================== 左侧：会话列表 ==================== -->
    <div class="conversation-list">
      <div class="list-header">
        <h3>会话列表</h3>
        <el-input
          v-model="searchQuery"
          :prefix-icon="Search"
          placeholder="搜索会话"
          size="small"
          clearable
        />
      </div>

      <div class="list-filters">
        <el-tag size="small" type="success">进行中 {{ stats.active_count }}</el-tag>
        <el-tag size="small" type="warning">等待 {{ stats.waiting_count }}</el-tag>
        <el-tag size="small" type="info">今日已结束 {{ stats.closed_count }}</el-tag>
      </div>

      <div class="list-body" @scroll="onSessionListScroll">
        <div
          v-for="conv in conversations"
          :key="conv.id"
          class="conversation-item"
          :class="{ active: activeConversation === conv.id }"
          @click="selectConversation(conv.id)"
        >
          <el-avatar :size="40" class="avatar">
            <el-icon><User /></el-icon>
          </el-avatar>

          <div class="conversation-info">
            <div class="conversation-top">
              <span class="name">{{ conv.session_no }}</span>
              <span class="time">{{ formatTime(conv.created_at) }}</span>
            </div>
            <div class="conversation-bottom">
              <span class="last-message">{{ conv.last_message || '暂无消息' }}</span>
            </div>
          </div>

          <el-badge
            v-if="conv.unread_count && conv.unread_count > 0"
            :value="conv.unread_count"
            class="unread-badge"
          />
        </div>
      </div>
    </div>

    <!-- ==================== 中间：会话详情 ==================== -->
    <div v-if="activeConversation" class="conversation-detail">
      <!-- 会话头部 -->
      <div class="detail-header">
        <div class="header-left">
          <el-avatar :size="36">
            <el-icon><User /></el-icon>
          </el-avatar>
          <div class="user-info">
            <h4>
              {{ sessionDetail?.user_info?.nickname || sessionDetail?.user_info?.username || '客户' }}
              <el-tag v-if="sessionMode === 'ai'" size="small" type="success" effect="plain">AI 模式</el-tag>
              <el-tag v-else size="small" type="warning" effect="plain">人工模式</el-tag>
            </h4>
            <span class="status-text" :style="{ color: wsStatusColor }">{{ wsStatusText }}</span>
          </div>
        </div>
        <div class="header-actions" v-if="sessionMode === 'agent'">
          <el-button size="small" type="success" @click="handleTransferToAI">转AI</el-button>
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
            </div>
            <div class="message-time">{{ formatTime(msg.created_at) }}</div>
          </div>
        </div>
      </div>

      <!-- 输入区域 -->
      <div class="chat-input-area">
        <div class="input-toolbar">
          <el-button size="small" plain>快捷回复</el-button>
          <el-button size="small" plain>表情</el-button>
          <el-button size="small" plain>图片</el-button>
        </div>

        <el-input
          v-model="inputMessage"
          type="textarea"
          :rows="3"
          placeholder="输入回复内容... (Ctrl+Enter 发送)"
          resize="none"
          @keydown.ctrl.enter="sendMessage"
        />

        <div class="input-actions">
          <el-button @click="sendMessage" type="primary">
            <el-icon><ChatDotRound /></el-icon>
            发送
          </el-button>
        </div>
      </div>
    </div>
    <div v-else class="chat-empty">
      <el-empty description="请从左侧选择一个客户会话" />
    </div>

  </div>
</template>

<style scoped>
.agent-conversations-container {
  display: flex;
  height: calc(100vh - 120px);
  background: #f5f7fa;
  gap: 1px;
}

/* ==================== 左侧：会话列表 ==================== */
.conversation-list {
  width: 280px;
  background: #fff;
  display: flex;
  flex-direction: column;
}

.list-header {
  padding: 16px;
  border-bottom: 1px solid #e4e7ed;
}

.list-header h3 {
  margin: 0 0 12px 0;
  font-size: 16px;
  font-weight: 600;
}

.list-filters {
  padding: 12px 16px;
  display: flex;
  gap: 8px;
  border-bottom: 1px solid #e4e7ed;
}

.list-body {
  flex: 1;
  overflow-y: auto;
}

.conversation-item {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  cursor: pointer;
  transition: background 0.2s;
  position: relative;
  border-bottom: 1px solid #f0f0f0;
}

.conversation-item:hover {
  background: #f5f7fa;
}

.conversation-item.active {
  background: #ecf5ff;
}

.conversation-info {
  flex: 1;
  margin-left: 12px;
  min-width: 0;
}

.conversation-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.conversation-top .name {
  font-weight: 500;
  font-size: 14px;
}

.conversation-top .time {
  font-size: 12px;
  color: #909399;
}

.conversation-bottom {
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

/* ==================== 中间：会话详情 ==================== */
.conversation-detail {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #fff;
}

.detail-header {
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

.user-info h4 {
  margin: 0;
  font-size: 16px;
}

.status-text {
  font-size: 12px;
  transition: color 0.3s;
}

.header-actions {
  display: flex;
  gap: 8px;
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

/* 用户消息显示在左侧 */
.message-item.user {
  flex-direction: row;
}

/* 客服自己的消息显示在右侧 */
.message-item.agent {
  flex-direction: row-reverse;
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

/* 用户消息样式 - 白色背景 */
.message-item.user .message-bubble {
  background: #fff;
  border: 1px solid #e4e7ed;
}

/* 客服消息样式 - 蓝色背景 */
.message-item.agent .message-bubble {
  background: #409eff;
  color: #fff;
}

.message-time {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
  padding: 0 4px;
}

/* 客服消息时间右对齐 */
.message-item.agent .message-time {
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
