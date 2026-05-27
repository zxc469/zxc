import http from './http'

/** 主体角色类型，与后端 PrincipalType 对齐 */
export type PrincipalType = 'user' | 'agent' | 'admin'

// ========== 请求 / 响应模型 ==========
export interface LoginRequest {
  username: string
  password: string
  principal_type: PrincipalType
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: 'Bearer'
  expires_in: number
  principal_type: PrincipalType
  principal_id: number
}

export interface PrincipalView {
  principal_type: PrincipalType
  principal_id: number
  username: string
  display_name: string | null
  email: string | null
  status: string
  /** 仅 admin 主体会返回：super_admin / admin */
  role_level?: 'super_admin' | 'admin' | null
}

export interface RegisterResponse {
  code: string
  message: string
  principal_type: PrincipalType
  principal_id: number
  username: string
}

export interface UserRegisterRequest {
  username: string
  password: string
  nickname?: string | null
  email?: string | null
  phone?: string | null
  avatar_url?: string | null
}

export interface AgentRegisterRequest {
  username: string
  password: string
  real_name: string
  email?: string | null
  phone?: string | null
  department?: string | null
  max_sessions?: number
}

/** super_admin 后台创建普通管理员入参（不含 role_level，后端强制为 admin）*/
export interface AdminCreateRequest {
  username: string
  password: string
  real_name: string
  email?: string | null
}

// ========== 接口调用 ==========
export function loginApi(body: LoginRequest) {
  return http.post<TokenResponse>('/auth/login', body, { skipAuth: true })
}

export function refreshApi(refresh_token: string) {
  return http.post<TokenResponse>('/auth/refresh', { refresh_token }, { skipAuth: true })
}

export function logoutApi(refresh_token: string) {
  // 登出接口即使失败也不影响前端清理状态
  return http.post('/auth/logout', { refresh_token }, { skipAuth: true })
}

export function meApi() {
  return http.get<PrincipalView>('/auth/me')
}

export function registerUserApi(body: UserRegisterRequest) {
  return http.post<RegisterResponse>('/auth/register/user', body, { skipAuth: true })
}

export function registerAgentApi(body: AgentRegisterRequest) {
  return http.post<RegisterResponse>('/auth/register/agent', body, { skipAuth: true })
}

/**
 * 超级管理员后台创建普通管理员（需已以 super_admin 登录）
 * 仅 super_admin 权限可用，无 skipAuth
 */
export function createAdminApi(body: AdminCreateRequest) {
  return http.post<RegisterResponse>('/auth/admins', body)
}
