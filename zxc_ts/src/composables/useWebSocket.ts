import { ref, onUnmounted } from 'vue'

type WsStatus = 'disconnected' | 'connecting' | 'connected' | 'error'

export interface WsMessage {
  type: string
  id: string
  sender: 'user' | 'agent' | 'ai' | 'system'
  content: string
  time: string
}

/**
 * WebSocket 连接管理可组合函数
 * 支持：断线自动重连（指数退避，最多5次）、连接状态追踪、消息收发
 * 参数：无（通过 connect(url) 传入目标地址）
 * 返回：{ status, lastMessage, connect, send, disconnect }
 */
export function useWebSocket() {
  const status = ref<WsStatus>('disconnected')
  const lastMessage = ref<WsMessage | null>(null)

  let ws: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let reconnectCount = 0
  let activeUrl = ''
  let activeProtocols: string[] = []
  const MAX_RECONNECT = 5

  function _doConnect() {
    if (!activeUrl) return
    status.value = 'connecting'
    ws = new WebSocket(activeUrl, activeProtocols)

    ws.onopen = () => {
      status.value = 'connected'
      reconnectCount = 0
    }

    ws.onmessage = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data) as WsMessage
        lastMessage.value = data
      } catch {
        // 忽略非 JSON 数据帧
      }
    }

    ws.onclose = () => {
      status.value = 'disconnected'
      if (reconnectCount < MAX_RECONNECT) {
        reconnectCount++
        reconnectTimer = setTimeout(() => _doConnect(), 3000 * reconnectCount)
      }
    }

    ws.onerror = () => {
      status.value = 'error'
      ws?.close()
    }
  }

  /**
   * 建立 WebSocket 连接（自动关闭上一个连接）
   * 参数：url - 完整的 ws:// 或 wss:// 地址
   * 参数：protocols - 可选的子协议数组，如 ["access_token.xxx"]，通过 Sec-WebSocket-Protocol 头传递
   */
  function connect(url: string, protocols?: string[]) {
    disconnect()
    activeUrl = url
    activeProtocols = protocols ?? []
    reconnectCount = 0
    _doConnect()
  }

  /**
   * 发送 JSON 消息帧
   * 参数：payload - 任意可序列化对象
   * 返回：true=发送成功，false=连接未就绪
   */
  function send(payload: object): boolean {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload))
      return true
    }
    return false
  }

  /** 主动断开连接并清理重连定时器 */
  function disconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    reconnectCount = MAX_RECONNECT // 阻止重连
    if (ws) {
      ws.onclose = null
      ws.close()
      ws = null
    }
    status.value = 'disconnected'
  }

  onUnmounted(() => disconnect())

  return { status, lastMessage, connect, send, disconnect }
}
