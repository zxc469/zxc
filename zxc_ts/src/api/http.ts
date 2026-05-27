import axios, { AxiosError, type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios'

/**
 * axios 请求配置扩展：
 * - skipAuth：不附加 Authorization 头（用于 login/refresh/logout/register 等非受保护接口）
 * - _retried：内部标记，避免 401 -> refresh -> 401 的无限循环
 */
declare module 'axios' {
  export interface AxiosRequestConfig {
    skipAuth?: boolean
    _retried?: boolean
  }
  export interface InternalAxiosRequestConfig {
    skipAuth?: boolean
    _retried?: boolean
  }
}

const http = axios.create({
  baseURL: '/api/v1',
  timeout: 15000
})

// ============================================================================
// 请求拦截器：自动附加 Authorization: Bearer <access_token>
// ============================================================================
http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (!config.skipAuth) {
    // 懒读 localStorage，避免与 Pinia store 形成循环依赖
    const token = localStorage.getItem('app_access_token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
  }
  return config
})

// ============================================================================
// 响应拦截器：401 时尝试使用 refresh_token 换新 access_token，并重放原请求
// 多个并发 401 会共享同一次 refresh 调用，避免重复刷新
// ============================================================================
let refreshingPromise: Promise<string | null> | null = null

async function performRefresh(): Promise<string | null> {
  const refresh = localStorage.getItem('app_refresh_token')
  if (!refresh) {
    return null
  }
  try {
    const res = await http.post<{
      access_token: string
      refresh_token: string
      principal_type: string
      principal_id: number
      expires_in: number
    }>('/auth/refresh', { refresh_token: refresh }, { skipAuth: true })
    const data = res.data
    localStorage.setItem('app_access_token', data.access_token)
    localStorage.setItem('app_refresh_token', data.refresh_token)
    localStorage.setItem('app_role', data.principal_type)
    localStorage.setItem('app_principal_id', String(data.principal_id))
    return data.access_token
  } catch {
    return null
  }
}

function clearLocalAuthAndRedirect() {
  localStorage.removeItem('app_access_token')
  localStorage.removeItem('app_refresh_token')
  localStorage.removeItem('app_role')
  localStorage.removeItem('app_principal_id')
  localStorage.removeItem('app_profile')
  // 当前已在 /login 时不再重复跳转，避免死循环
  if (!location.pathname.startsWith('/login')) {
    const redirect = encodeURIComponent(location.pathname + location.search)
    location.replace(`/login?redirect=${redirect}`)
  }
}

// 响应成功拦截器：自动解包后端统一 ApiResponse 信封 { code, message, data } → data
http.interceptors.response.use(
  (response) => {
    const body = response.data
    if (body && typeof body === 'object' && 'code' in body && 'data' in body) {
      response.data = body.data
    }
    return response
  },
  async (error: AxiosError) => {
    const original = error.config as InternalAxiosRequestConfig | undefined
    const status = error.response?.status
    // 非 401、无原始请求、已重试过、或本就不带 auth（refresh/login）则直接抛
    if (status !== 401 || !original || original._retried || original.skipAuth) {
      return Promise.reject(error)
    }
    original._retried = true

    // 共享同一次刷新
    if (!refreshingPromise) {
      refreshingPromise = performRefresh().finally(() => {
        refreshingPromise = null
      })
    }
    const newToken = await refreshingPromise
    if (!newToken) {
      clearLocalAuthAndRedirect()
      return Promise.reject(error)
    }
    // 使用新 token 重放原请求
    original.headers.Authorization = `Bearer ${newToken}`
    return http.request(original as AxiosRequestConfig)
  }
)

export default http
