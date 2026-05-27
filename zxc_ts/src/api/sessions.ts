import http from './http'

// ==================== 类型定义 ====================
export type SessionStatus = 'waiting' | 'assigned' | 'active' | 'closed' | 'transferred'
export type SessionSource = 'user_initiated' | 'ai_transfer' | 'admin_assign'
export type SenderType = 'user' | 'agent' | 'ai' | 'system'
export type MessageType = 'text' | 'image' | 'file' | 'system_event'

export type HandledBy = 'ai' | 'agent'

// 会话基本信息（列表用）
export interface SessionListItem {
  id: number
  session_no: string
  user_id: number
  agent_id: number | null
  status: SessionStatus
  handled_by: HandledBy
  source: SessionSource
  priority: number
  created_at: string
  assigned_at: string | null
  active_at: string | null
  closed_at: string | null
  rating: number | null
  // 前端计算字段
  user_name?: string
  agent_name?: string
  last_message?: string
  last_message_time?: string
  unread_count?: number
}

// 会话详情（点击后加载）
export interface SessionDetail extends SessionListItem {
  user_info?: {
    id: number
    username: string
    nickname: string | null
    email: string | null
    phone: string | null
  }
  agent_info?: {
    id: number
    username: string
    real_name: string
    department: string | null
  }
  close_reason?: string
  rating_comment?: string
}

// 消息列表项
export interface MessageItem {
  id: number
  session_id: number
  sender_type: SenderType
  sender_id: number | null
  message_type: MessageType
  content: string
  is_read: boolean
  read_at: string | null
  created_at: string
  metadata: Record<string, any> | null
}

// 创建会话请求
export interface CreateSessionRequest {
  source?: SessionSource
  priority?: number
}

// 发送消息请求
export interface SendMessageRequest {
  message_type: MessageType
  content: string
  metadata?: Record<string, any>
}

// 会话列表响应（分页）
export interface SessionListResponse {
  total: number
  page: number
  page_size: number
  items: SessionListItem[]
}

// 消息列表响应（分页）
export interface MessageListResponse {
  total: number
  page: number
  page_size: number
  has_more: boolean
  items: MessageItem[]
}

// AI 对话响应
export interface ChatResponse {
  answer: string
  ticket_id?: string | null
  should_handoff_to_human: boolean
}

// ==================== 用户端接口 ====================

/**
 * 获取我的会话列表
 */
export function getUserSessions(params: {
  page?: number
  page_size?: number
  status?: SessionStatus
}) {
  return http.get<SessionListResponse>('/user/sessions', { params })
}

/**
 * 创建新会话
 */
export function createSession(body: CreateSessionRequest = {}) {
  return http.post<SessionDetail>('/user/sessions', body)
}

/**
 * 获取会话详情
 */
export function getSessionDetail(sessionId: number) {
  return http.get<SessionDetail>(`/user/sessions/${sessionId}`)
}

/**
 * 获取会话消息列表（支持分页）
 */
export function getSessionMessages(sessionId: number, params: {
  page?: number
  page_size?: number
  before_id?: number // 加载更早的消息
}) {
  return http.get<MessageListResponse>(`/user/sessions/${sessionId}/messages`, { params })
}

/**
 * 发送消息（人工客服模式）
 */
export function sendSessionMessage(sessionId: number, body: SendMessageRequest) {
  return http.post<MessageItem>(`/user/sessions/${sessionId}/messages`, body)
}

/**
 * AI 智能客服对话（AI 模式）- 同步版
 */
export function chatWithAI(sessionId: number, body: SendMessageRequest) {
  return http.post<ChatResponse>(`/user/sessions/${sessionId}/chat`, body)
}

// SSE 事件类型
export interface SSETokenEvent {
  token: string
  seq: number
}

export interface SSEToolCallEvent {
  tool_name: string
  status: 'running' | 'done'
}

export interface SSEDoneEvent {
  answer: string
  need_human: boolean
}

export interface SSEErrorEvent {
  message: string
}

export type SSEEventType = 'token' | 'tool_call' | 'done' | 'error' | 'message_start'

export interface SSEEvent {
  event: SSEEventType
  data: SSETokenEvent | SSEToolCallEvent | SSEDoneEvent | SSEErrorEvent
}

/**
 * AI 智能客服 SSE 流式对话。
 *
 * 使用 fetch 发送 POST，通过 ReadableStream 解析 SSE 响应，
 * 每收到一个事件即调用 onEvent 回调，流结束或异常时 resolve/reject。
 *
 * 返回一个 abort 函数，调用后可中断请求和流读取。
 */
export function chatWithAIStream(
  sessionId: number,
  body: SendMessageRequest,
  onEvent: (event: SSEEvent) => void,
): () => void {
  const controller = new AbortController()
  const token = localStorage.getItem('app_access_token') || ''

  const url = `/api/v1/user/sessions/${sessionId}/chat/stream`

  fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const text = await response.text().catch(() => '')
        onEvent({ event: 'error', data: { message: `HTTP ${response.status}: ${text}` } })
        return
      }

      const reader = response.body?.getReader()
      if (!reader) {
        onEvent({ event: 'error', data: { message: '浏览器不支持流式读取' } })
        return
      }

      const decoder = new TextDecoder()
      let buffer = ''

      try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })

          // 解析 SSE 帧：按 \n\n 分割
          const parts = buffer.split('\n\n')
          buffer = parts.pop() || ''

          for (const part of parts) {
            if (!part.trim()) continue
            const lines = part.split('\n')
            let eventType = ''
            let dataStr = ''

            for (const line of lines) {
              if (line.startsWith('event: ')) {
                eventType = line.slice(7).trim()
              } else if (line.startsWith('data: ')) {
                dataStr = line.slice(6)
              }
            }

            if (!eventType || !dataStr) continue

            try {
              const data = JSON.parse(dataStr)
              onEvent({ event: eventType as SSEEventType, data })
            } catch {
              // 忽略解析失败的数据帧
            }
          }
        }
      } catch (err: any) {
        if (err.name !== 'AbortError') {
          onEvent({ event: 'error', data: { message: err.message || '流读取异常' } })
        }
      }
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        onEvent({ event: 'error', data: { message: err.message || '网络请求失败' } })
      }
    })

  return () => controller.abort()
}

/**
 * 标记消息已读
 */
export function markSessionRead(sessionId: number) {
  return http.put(`/user/sessions/${sessionId}/read`)
}

/**
 * 请求转人工客服
 */
export function transferToAgent(sessionId: number) {
  return http.post(`/user/sessions/${sessionId}/transfer`)
}

/**
 * 结束会话
 */
export function closeSession(sessionId: number, body: {
  close_reason?: string
}) {
  return http.post(`/user/sessions/${sessionId}/close`, body)
}

/**
 * 提交评价
 */
export function rateSession(sessionId: number, body: {
  rating: number
  comment?: string
}) {
  return http.put(`/user/sessions/${sessionId}/rate`, body)
}

// ==================== 客服端接口 ====================

/**
 * 获取我的会话列表
 */
export function getAgentSessions(params: {
  page?: number
  page_size?: number
  status?: SessionStatus
}) {
  return http.get<SessionListResponse>('/agent/sessions', { params })
}

/**
 * 获取会话详情
 */
export function getAgentSessionDetail(sessionId: number) {
  return http.get<SessionDetail>(`/agent/sessions/${sessionId}`)
}

/**
 * 获取会话消息列表
 */
export function getAgentSessionMessages(sessionId: number, params: {
  page?: number
  page_size?: number
  before_id?: number
}) {
  return http.get<MessageListResponse>(`/agent/sessions/${sessionId}/messages`, { params })
}

/**
 * 发送消息
 */
export function sendAgentMessage(sessionId: number, body: SendMessageRequest) {
  return http.post<MessageItem>(`/agent/sessions/${sessionId}/messages`, body)
}

/**
 * 接受会话
 */
export function acceptSession(sessionId: number) {
  return http.put(`/agent/sessions/${sessionId}/accept`)
}

/**
 * 标记消息已读
 */
export function markAgentSessionRead(sessionId: number) {
  return http.put(`/agent/sessions/${sessionId}/read`)
}

/**
 * 转接会话
 */
export function transferSession(sessionId: number, body: {
  to_agent_id: number
  reason?: string
}) {
  return http.put(`/agent/sessions/${sessionId}/transfer`, body)
}

/**
 * 转AI模式
 */
export function transferToAI(sessionId: number) {
  return http.put(`/agent/sessions/${sessionId}/transfer-to-ai`)
}

/**
 * 结束会话
 */
export function closeAgentSession(sessionId: number, body: {
  close_reason?: string
}) {
  return http.put(`/agent/sessions/${sessionId}/close`, body)
}

/**
 * 获取会话统计
 */
export function getAgentSessionStats() {
  return http.get<{
    total_sessions: number
    active_sessions: number
    waiting_sessions: number
    closed_today: number
    avg_response_time: number
  }>('/agent/sessions/stats')
}
