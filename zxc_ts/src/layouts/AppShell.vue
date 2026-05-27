<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useAppStore, type UserRole } from '../stores/app'
import { logoutApi, meApi } from '../api/auth'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()

const active = computed(() => String(route.meta.nav || route.path))

// 导航项按角色可见性标注，菜单会根据当前登录角色动态过滤
interface NavItem {
  key: string
  label: string
  path: string
  roles: UserRole[]
}

const allNavItems: NavItem[] = [
  { key: '/user/chat', label: '用户会话', path: '/user/chat', roles: ['user'] },
  { key: '/user/tickets', label: '用户工单', path: '/user/tickets', roles: ['user'] },
  { key: '/agent/conversations', label: '客服会话', path: '/agent/conversations', roles: ['agent'] },
  { key: '/agent/tickets', label: '客服工单', path: '/agent/tickets', roles: ['agent'] },
  { key: '/admin/dashboard', label: '管理看板', path: '/admin/dashboard', roles: ['admin'] },
  { key: '/admin/knowledge', label: '知识库', path: '/admin/knowledge', roles: ['admin'] }
]

const navItems = computed(() =>
  allNavItems.filter((item) => item.roles.includes(appStore.role))
)

const roleLabelMap: Record<UserRole, string> = {
  user: '普通用户',
  agent: '客服人员',
  admin: '管理员'
}

const roleTagType = computed<'primary' | 'success' | 'warning'>(() => {
  if (appStore.role === 'admin') return 'warning'
  if (appStore.role === 'agent') return 'success'
  return 'primary'
})

// 右上角展示：优先 display_name，回退 username；Avatar 使用首字母作为占位
const displayName = computed(() => appStore.displayName || '未命名')
const avatarText = computed(() => {
  const name = appStore.displayName || appStore.username || '?'
  return name.slice(0, 1).toUpperCase()
})
const emailText = computed(() => appStore.profile?.email ?? '')

async function handleLogout() {
  const refresh = appStore.refreshToken
  try {
    if (refresh) await logoutApi(refresh)
  } catch {
    // 后端登出失败也继续清理本地态，避免卡住用户
  } finally {
    appStore.clearAuth()
    router.replace('/login')
  }
}

// 应用壳挂载时主动拉取一次 /auth/me：
// 1) 验证本地 token 仍然有效（若失效则由 http 拦截器自动刷新或登出）
// 2) 刷新主体资料，保证整页刷新后 UI 信息最新
onMounted(async () => {
  if (!appStore.isAuthed) return
  try {
    const res = await meApi()
    appStore.setProfile(res.data)
  } catch {
    // 401 会被 http 拦截器统一处理；其他错误静默，不阻塞 UI
    ElMessage.warning('登录状态已失效，请重新登录')
  }
})
</script>

<template>
  <el-container class="shell">
    <el-aside width="220px" class="shell-aside">
      <div class="brand">智能客服系统</div>
      <el-menu :default-active="active" router>
        <el-menu-item v-for="item in navItems" :key="item.key" :index="item.path">
          {{ item.label }}
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="shell-header">
        <span>前端 MVP 骨架（单应用多角色路由）</span>
        <div class="shell-header-right">
          <el-tag :type="roleTagType" effect="light">{{ roleLabelMap[appStore.role] }}</el-tag>
          <el-avatar :size="28" class="avatar">{{ avatarText }}</el-avatar>
          <div class="user-info">
            <div class="user-name">{{ displayName }}</div>
            <div v-if="emailText" class="user-sub">{{ emailText }}</div>
          </div>
          <el-button type="danger" link @click="handleLogout">退出登录</el-button>
        </div>
      </el-header>
      <el-main>
        <router-view v-slot="{ Component }">
          <keep-alive>
            <component :is="Component" />
          </keep-alive>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.shell {
  min-height: 100vh;
}

.shell-aside {
  border-right: 1px solid #f0f0f0;
}

.brand {
  padding: 16px;
  font-size: 16px;
  font-weight: 600;
}

.shell-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #f0f0f0;
  font-weight: 500;
}

.shell-header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.avatar {
  background: #409eff;
  color: #fff;
  font-weight: 600;
}

.user-info {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
}

.user-name {
  font-size: 13px;
  color: #303133;
}

.user-sub {
  font-size: 11px;
  color: #909399;
}
</style>
