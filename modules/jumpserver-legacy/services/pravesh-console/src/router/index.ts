import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      redirect: '/dashboard',
    },
    {
      path: '/login',
      name: 'Login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true },
    },
    {
      path: '/dashboard',
      name: 'Dashboard',
      component: () => import('@/views/HomeView.vue'),
    },
    {
      path: '/playbooks',
      name: 'Playbooks',
      component: () => import('@/views/PlaybookListView.vue'),
    },
    {
      path: '/playbooks/new',
      name: 'NewPlaybook',
      component: () => import('@/views/PlaybookBuilderView.vue'),
    },
    {
      path: '/playbooks/:id',
      name: 'EditPlaybook',
      component: () => import('@/views/PlaybookBuilderView.vue'),
    },
    {
      path: '/templates',
      name: 'Templates',
      component: () => import('@/views/TemplateLibraryView.vue'),
    },
    {
      path: '/templates/:slug',
      name: 'TemplateDetail',
      component: () => import('@/views/TemplateDetailView.vue'),
    },
    {
      path: '/jobs',
      name: 'Jobs',
      component: () => import('@/views/JobsView.vue'),
    },
    {
      path: '/jobs/:id',
      name: 'JobDetail',
      component: () => import('@/views/JobDetailView.vue'),
    },
    {
      path: '/assets',
      name: 'Assets',
      component: () => import('@/views/AssetsView.vue'),
    },
    {
      path: '/sessions',
      name: 'Sessions',
      component: () => import('@/views/SessionsView.vue'),
    },
    {
      path: '/terminal',
      name: 'SSHTerminal',
      component: () => import('@/views/SSHTerminalView.vue'),
      meta: { fullscreen: true },
    },
    {
      path: '/sessions/:id/terminal',
      redirect: (to) => ({ name: 'SSHTerminal', query: { session: to.params.id } }),
    },
    {
      path: '/alerts',
      name: 'Alerts',
      component: () => import('@/views/AlertsView.vue'),
    },
    {
      path: '/settings',
      name: 'Settings',
      component: () => import('@/views/SettingsView.vue'),
    },
  ],
})

router.beforeEach(async (to) => {
  if (to.meta.public) return true

  const auth = useAuthStore()
  if (!auth.token) {
    return { name: 'Login', query: { redirect: to.fullPath } }
  }
  if (!auth.user) {
    const ok = await auth.checkToken()
    if (!ok) {
      return { name: 'Login', query: { redirect: to.fullPath } }
    }
  }
  return true
})

export default router
