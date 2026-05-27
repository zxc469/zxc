import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import type { PrincipalView, PrincipalType, TokenResponse } from '../api/auth'

/** 向后兼容：路由守卫与 UI 使用的角色别名 */
export type UserRole = PrincipalType

// localStorage 键名统一常量，避免硬编码
const K_ACCESS = 'app_access_token'
const K_REFRESH = 'app_refresh_token'
const K_ROLE = 'app_role'
const K_PRINCIPAL_ID = 'app_principal_id'
const K_PROFILE = 'app_profile'

function readRole(): UserRole {
  const raw = localStorage.getItem(K_ROLE)
  if (raw === 'user' || raw === 'agent' || raw === 'admin') return raw
  return 'user'
}

function readProfile(): PrincipalView | null {
  try {
    const raw = localStorage.getItem(K_PROFILE)
    return raw ? (JSON.parse(raw) as PrincipalView) : null
  } catch {
    return null
  }
}

export const useAppStore = defineStore('app', () => {
  // ========== 状态（从 localStorage 恢复以支持整页刷新）==========
  const accessToken = ref<string>(localStorage.getItem(K_ACCESS) ?? '')
  const refreshToken = ref<string>(localStorage.getItem(K_REFRESH) ?? '')
  const role = ref<UserRole>(readRole())
  const principalId = ref<number>(Number(localStorage.getItem(K_PRINCIPAL_ID) ?? '0') || 0)
  const profile = ref<PrincipalView | null>(readProfile())

  // ========== 计算属性 ==========
  const isAuthed = computed(() => accessToken.value.length > 0)
  const displayName = computed(
    () => profile.value?.display_name || profile.value?.username || ''
  )
  const username = computed(() => profile.value?.username ?? '')

  // ========== 行为 ==========
  /** 登录成功后：保存双 token + 基础角色信息 */
  function setTokens(resp: TokenResponse) {
    accessToken.value = resp.access_token
    refreshToken.value = resp.refresh_token
    role.value = resp.principal_type
    principalId.value = resp.principal_id
    localStorage.setItem(K_ACCESS, resp.access_token)
    localStorage.setItem(K_REFRESH, resp.refresh_token)
    localStorage.setItem(K_ROLE, resp.principal_type)
    localStorage.setItem(K_PRINCIPAL_ID, String(resp.principal_id))
  }

  /** 拉取 /auth/me 后：更新主体资料 */
  function setProfile(view: PrincipalView) {
    profile.value = view
    role.value = view.principal_type
    principalId.value = view.principal_id
    localStorage.setItem(K_ROLE, view.principal_type)
    localStorage.setItem(K_PRINCIPAL_ID, String(view.principal_id))
    localStorage.setItem(K_PROFILE, JSON.stringify(view))
  }

  /** 清理所有登录态 */
  function clearAuth() {
    accessToken.value = ''
    refreshToken.value = ''
    role.value = 'user'
    principalId.value = 0
    profile.value = null
    localStorage.removeItem(K_ACCESS)
    localStorage.removeItem(K_REFRESH)
    localStorage.removeItem(K_ROLE)
    localStorage.removeItem(K_PRINCIPAL_ID)
    localStorage.removeItem(K_PROFILE)
  }

  return {
    // 状态
    accessToken,
    refreshToken,
    role,
    principalId,
    profile,
    // 计算
    isAuthed,
    displayName,
    username,
    // 行为
    setTokens,
    setProfile,
    clearAuth
  }
})
