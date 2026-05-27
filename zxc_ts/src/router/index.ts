import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";
import AppShell from "../layouts/AppShell.vue";
import LoginView from "../views/LoginView.vue";
import UserChatView from "../views/user/UserChatView.vue";
import UserTicketsView from "../views/user/UserTicketsView.vue";
import UserTicketDetailView from "../views/user/UserTicketDetailView.vue";
import AgentConversationsView from "../views/agent/AgentConversationsView.vue";
import AgentTicketsView from "../views/agent/AgentTicketsView.vue";
import AdminDashboardView from "../views/admin/AdminDashboardView.vue";
import AdminKnowledgeView from "../views/admin/AdminKnowledgeView.vue";
import { useAppStore, type UserRole } from "../stores/app";

// 各角色登录后的默认首页，供路由守卫使用
const ROLE_HOME: Record<UserRole, string> = {
  user: "/user/chat",
  agent: "/agent/conversations",
  admin: "/admin/dashboard"
};

const routes: RouteRecordRaw[] = [
  { path: "/login", component: LoginView, meta: { public: true } },
  {
    path: "/",
    component: AppShell,
    children: [
      { path: "", redirect: "/user/chat" },
      { path: "user/chat", component: UserChatView, meta: { nav: "/user/chat", roles: ["user"] } },
      { path: "user/tickets", component: UserTicketsView, meta: { nav: "/user/tickets", roles: ["user"] } },
      { path: "user/tickets/:id", component: UserTicketDetailView, meta: { nav: "/user/tickets", roles: ["user"] } },
      { path: "agent/conversations", component: AgentConversationsView, meta: { nav: "/agent/conversations", roles: ["agent"] } },
      { path: "agent/tickets", component: AgentTicketsView, meta: { nav: "/agent/tickets", roles: ["agent"] } },
      { path: "admin/dashboard", component: AdminDashboardView, meta: { nav: "/admin/dashboard", roles: ["admin"] } },
      { path: "admin/knowledge", component: AdminKnowledgeView, meta: { nav: "/admin/knowledge", roles: ["admin"] } }
    ]
  }
];

const router = createRouter({
  history: createWebHistory(),
  routes
});

// 全局前置守卫：未登录强制跳 /login；已登录访问越权路由则重定向到角色首页
router.beforeEach((to) => {
  const appStore = useAppStore();
  if (to.meta.public) {
    if (to.path === "/login" && appStore.isAuthed) {
      return ROLE_HOME[appStore.role];
    }
    return true;
  }
  if (!appStore.isAuthed) {
    return { path: "/login", query: { redirect: to.fullPath } };
  }
  const allowed = to.meta.roles as UserRole[] | undefined;
  if (allowed && !allowed.includes(appStore.role)) {
    return ROLE_HOME[appStore.role];
  }
  return true;
});

export default router;
