<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { isAxiosError } from 'axios'
import { useAppStore, type UserRole } from '../stores/app'
import { loginApi, meApi, registerUserApi, registerAgentApi } from '../api/auth'

type FormMode = 'login' | 'register'

const router = useRouter()
const route = useRoute()
const appStore = useAppStore()

// 表单模型：账号、密码、角色下拉（user/agent/admin）
const loginForm = reactive({
  username: '',
  password: '',
  principal_type: 'user' as UserRole
})

// 注册表单模型
const registerForm = reactive({
  username: '',
  password: '',
  confirmPassword: '',
  principal_type: 'user' as UserRole,
  // 用户注册字段
  nickname: '',
  email: '',
  phone: '',
  // 客服注册字段
  real_name: '',
  department: ''
})

const submitting = ref(false)
const formMode = ref<FormMode>('login')

// 各角色登录后的默认跳转目标
const roleHomeMap: Record<UserRole, string> = {
  user: '/user/chat',
  agent: '/agent/conversations',
  admin: '/admin/dashboard'
}

// 将后端错误结构统一提取为提示文本
function extractErrorMessage(err: unknown, fallback = '操作失败'): string {
  if (isAxiosError(err)) {
    const data = err.response?.data as { detail?: { code?: string; message?: string } | string } | undefined
    if (data?.detail) {
      if (typeof data.detail === 'string') return data.detail
      if (data.detail.message) return data.detail.message
    }
    if (err.response?.status === 422) return '请填写合法的信息'
    if (err.message) return err.message
  }
  return fallback
}

async function handleLogin() {
  if (!loginForm.username.trim() || !loginForm.password.trim()) {
    ElMessage.warning('请填写账号和密码')
    return
  }
  submitting.value = true
  try {
    const loginRes = await loginApi({
      username: loginForm.username.trim(),
      password: loginForm.password,
      principal_type: loginForm.principal_type
    })
    appStore.setTokens(loginRes.data)

    // 登录后立刻拉一次主体视图，UI 侧显示用户名 / 真实姓名等
    try {
      const meRes = await meApi()
      appStore.setProfile(meRes.data)
    } catch {
      // /me 失败不影响进入系统（token 已下发，守卫放行）
    }

    ElMessage.success('登录成功')
    // 支持路由守卫回跳
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : ''
    router.replace(redirect || roleHomeMap[loginRes.data.principal_type])
  } catch (err) {
    ElMessage.error(extractErrorMessage(err))
  } finally {
    submitting.value = false
  }
}

async function handleRegister() {
  // 基础校验
  if (!registerForm.username.trim() || !registerForm.password.trim()) {
    ElMessage.warning('请填写账号和密码')
    return
  }
  if (registerForm.password !== registerForm.confirmPassword) {
    ElMessage.warning('两次输入的密码不一致')
    return
  }
  if (registerForm.password.length < 6) {
    ElMessage.warning('密码长度不能少于6位')
    return
  }
  
  // 管理员不允许直接注册
  if (registerForm.principal_type === 'admin') {
    ElMessage.warning('管理员账号不支持直接注册，请联系超级管理员创建')
    return
  }

  submitting.value = true
  try {
    if (registerForm.principal_type === 'user') {
      // 普通用户注册
      await registerUserApi({
        username: registerForm.username.trim(),
        password: registerForm.password,
        nickname: registerForm.nickname.trim() || null,
        email: registerForm.email.trim() || null,
        phone: registerForm.phone.trim() || null
      })
    } else if (registerForm.principal_type === 'agent') {
      // 客服注册
      if (!registerForm.real_name.trim()) {
        ElMessage.warning('请填写真实姓名')
        return
      }
      await registerAgentApi({
        username: registerForm.username.trim(),
        password: registerForm.password,
        real_name: registerForm.real_name.trim(),
        email: registerForm.email.trim() || null,
        phone: registerForm.phone.trim() || null,
        department: registerForm.department.trim() || null
      })
    }

    ElMessage.success('注册成功，请登录')
    // 注册成功后切换到登录模式
    formMode.value = 'login'
    // 清空注册表单
    Object.assign(registerForm, {
      username: '',
      password: '',
      confirmPassword: '',
      principal_type: 'user',
      nickname: '',
      email: '',
      phone: '',
      real_name: '',
      department: ''
    })
  } catch (err) {
    ElMessage.error(extractErrorMessage(err, '注册失败'))
  } finally {
    submitting.value = false
  }
}

function switchMode(mode: FormMode) {
  formMode.value = mode
}
</script>

<template>
  <el-row justify="center" style="margin-top: 120px">
    <el-col :span="8" :xs="22" :sm="14" :md="10">
      <el-card>
        <h2 class="page-title">{{ formMode === 'login' ? '登录' : '注册' }}</h2>
        
        <!-- 登录表单 -->
        <el-form v-if="formMode === 'login'" label-position="top" @submit.prevent="handleLogin">
          <el-form-item label="账号">
            <el-input
              v-model="loginForm.username"
              placeholder="请输入账号"
              autocomplete="username"
              @keyup.enter="handleLogin"
            />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="loginForm.password"
              type="password"
              placeholder="请输入密码"
              autocomplete="current-password"
              show-password
              @keyup.enter="handleLogin"
            />
          </el-form-item>
          <el-form-item label="角色">
            <el-select v-model="loginForm.principal_type">
              <el-option label="普通用户" value="user" />
              <el-option label="客服人员" value="agent" />
              <el-option label="管理员" value="admin" />
            </el-select>
          </el-form-item>
          <el-button
            type="primary"
            style="width: 100%"
            :loading="submitting"
            @click="handleLogin"
          >
            进入系统
          </el-button>
          <div style="text-align: center; margin-top: 16px">
            <el-link type="primary" @click="switchMode('register')">没有账号？立即注册</el-link>
          </div>
        </el-form>
        
        <!-- 注册表单 -->
        <el-form v-else label-position="top" @submit.prevent="handleRegister">
          <el-form-item label="账号">
            <el-input
              v-model="registerForm.username"
              placeholder="请输入账号"
              autocomplete="username"
              @keyup.enter="handleRegister"
            />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="registerForm.password"
              type="password"
              placeholder="请输入密码（至少6位）"
              autocomplete="new-password"
              show-password
              @keyup.enter="handleRegister"
            />
          </el-form-item>
          <el-form-item label="确认密码">
            <el-input
              v-model="registerForm.confirmPassword"
              type="password"
              placeholder="请再次输入密码"
              autocomplete="new-password"
              show-password
              @keyup.enter="handleRegister"
            />
          </el-form-item>
          <el-form-item label="角色">
            <el-select v-model="registerForm.principal_type">
              <el-option label="普通用户" value="user" />
              <el-option label="客服人员" value="agent" />
            </el-select>
            <div style="color: #909399; font-size: 12px; margin-top: 4px">
              管理员账号不支持直接注册，请联系超级管理员创建
            </div>
          </el-form-item>
          
          <!-- 用户注册额外字段 -->
          <template v-if="registerForm.principal_type === 'user'">
            <el-form-item label="昵称">
              <el-input
                v-model="registerForm.nickname"
                placeholder="请输入昵称（可选）"
              />
            </el-form-item>
          </template>
          
          <!-- 客服注册额外字段 -->
          <template v-if="registerForm.principal_type === 'agent'">
            <el-form-item label="真实姓名">
              <el-input
                v-model="registerForm.real_name"
                placeholder="请输入真实姓名"
              />
            </el-form-item>
            <el-form-item label="部门">
              <el-input
                v-model="registerForm.department"
                placeholder="请输入部门（可选）"
              />
            </el-form-item>
          </template>
          
          <!-- 通用额外字段 -->
          <el-form-item label="邮箱">
            <el-input
              v-model="registerForm.email"
              type="email"
              placeholder="请输入邮箱（可选）"
            />
          </el-form-item>
          <el-form-item label="手机号">
            <el-input
              v-model="registerForm.phone"
              placeholder="请输入手机号（可选）"
            />
          </el-form-item>
          
          <el-button
            type="primary"
            style="width: 100%"
            :loading="submitting"
            @click="handleRegister"
          >
            注册
          </el-button>
          <div style="text-align: center; margin-top: 16px">
            <el-link type="primary" @click="switchMode('login')">已有账号？立即登录</el-link>
          </div>
        </el-form>
      </el-card>
    </el-col>
  </el-row>
</template>
