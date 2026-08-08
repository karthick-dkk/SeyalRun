<template>
  <div class="app-shell">
    <!-- Sidebar — hidden when embedded inside Zabbix (its native menu provides navigation) -->
    <nav v-if="!isEmbedded" class="sidebar" :class="{ collapsed }">
      <div class="sidebar-logo">
        <router-link to="/" class="sidebar-logo-link" title="Go to Dashboard">
          <span class="logo-icon" v-html="ICONS.logo" />
          <span class="logo-text">SeyalRun</span>
        </router-link>
        <button class="sidebar-collapse-btn" @click="collapsed = !collapsed" :title="collapsed ? 'Expand' : 'Collapse'">
          <span v-html="collapsed ? ICONS.chevronRight : ICONS.chevronLeft" />
        </button>
      </div>
      <div class="sidebar-nav">
        <router-link v-if="auth.can('dashboard')" to="/"         class="nav-item" active-class="active"><span class="nav-icon" v-html="ICONS.dashboard" /><span class="nav-label">Dashboard</span></router-link>
        <router-link v-if="auth.can('hosts')"     to="/hosts"    class="nav-item" active-class="active"><span class="nav-icon" v-html="ICONS.hosts" /><span class="nav-label">Hosts</span></router-link>
        <router-link v-if="auth.can('assets')"    to="/assets"   class="nav-item" active-class="active"><span class="nav-icon" v-html="ICONS.assets" /><span class="nav-label">Assets</span></router-link>
        <router-link v-if="auth.can('zones')"     to="/zones"    class="nav-item" active-class="active"><span class="nav-icon" v-html="ICONS.globe" /><span class="nav-label">Zones</span></router-link>
        <router-link v-if="auth.can('sessions')"  to="/sessions" class="nav-item" active-class="active"><span class="nav-icon" v-html="ICONS.sessions" /><span class="nav-label">Sessions</span></router-link>
        <router-link v-if="auth.can('automation')" to="/automation" class="nav-item" active-class="active"><span class="nav-icon" v-html="ICONS.automation" /><span class="nav-label">Automation</span></router-link>
        <!-- One nav, not two: Admin is an inline expandable tree (grouped into Access /
             Inventory / Automation / Platform, matching AdminView's own grouping) instead of
             a link that opens a second, separate nav panel next to this one. Collapsed by
             default; auto-expands when already on an admin page so the active item isn't
             hidden behind a click. -->
        <template v-if="auth.canAnyAdmin()">
          <button type="button" class="nav-item nav-admin-toggle" :class="{ active: onAdminRoute }" @click="adminExpanded = !adminExpanded">
            <span class="nav-icon" v-html="ICONS.gear" /><span class="nav-label">Admin</span>
            <span class="nav-chevron" :class="{ expanded: adminExpanded }" v-html="ICONS.chevronRight" />
          </button>
          <div v-if="adminExpanded && !collapsed" class="nav-admin-groups">
            <template v-for="g in ADMIN_GROUPS" :key="g.label">
              <div v-if="g.tabs.some(t => auth.can(t.area))" class="nav-admin-group">
                <span class="nav-group-label">{{ g.label }}</span>
                <router-link
                  v-for="t in g.tabs.filter(t => auth.can(t.area))"
                  :key="t.to"
                  :to="t.to"
                  class="nav-item nav-sub"
                  active-class="active"
                >
                  <span class="nav-icon" v-html="t.icon" /><span class="nav-label">{{ t.label }}</span>
                </router-link>
              </div>
            </template>
          </div>
        </template>
      </div>
      <router-link to="/security" class="sidebar-user" style="text-decoration:none;color:inherit" title="Security — MFA settings">
        <div class="avatar">{{ (auth.user?.username || '?').charAt(0).toUpperCase() }}</div>
        <div class="user-meta">
          <div class="user-name">{{ auth.user?.username }}</div>
          <div class="user-role">{{ auth.roles.join(', ') || auth.user?.role_name }}</div>
        </div>
      </router-link>
      <div class="sidebar-logout">
        <button class="btn btn-sm" @click="doLogout">
          <span class="nav-label">Logout</span>
        </button>
      </div>
      <div v-if="appVersion" class="sidebar-version">v{{ appVersion }}</div>
    </nav>

    <!-- Main -->
    <div class="main-content">
      <div v-if="!isEmbedded" class="topbar">
        <span class="topbar-title">{{ $route.meta.title || 'SeyalRun' }}</span>
        <router-link
          v-if="zabbixTokenWarning"
          to="/settings/integration"
          class="topbar-link"
          style="color:var(--warn);border-color:rgba(210,153,34,0.4);background:rgba(210,153,34,0.08)"
          title="Zabbix API token is missing or invalid — host sync and Zabbix reachability checks won't work until it's fixed"
        >
          ⚠ Zabbix API token issue
        </router-link>
        <button type="button" class="topbar-icon-btn" @click="toggleTheme" :title="theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'">
          <span v-html="theme === 'dark' ? ICONS.sun : ICONS.moon" style="display:flex;align-items:center;" />
        </button>
        <div class="notif-wrap">
          <button type="button" class="topbar-icon-btn" @click="toggleNotifDropdown" title="Notifications" style="position:relative">
            <span v-html="ICONS.bell" style="display:flex;align-items:center;" />
            <span v-if="notifStore.unreadCount" class="notif-badge">{{ notifStore.unreadCount > 9 ? '9+' : notifStore.unreadCount }}</span>
          </button>
          <div v-if="showNotifDropdown" class="notif-backdrop" @click="showNotifDropdown = false" />
          <div v-if="showNotifDropdown" class="notif-dropdown">
            <template v-if="capturesStore.active.length">
              <div class="notif-dropdown-header">
                <span>Screen Captures <span class="notif-capture-hint">(expire after 1h)</span></span>
              </div>
              <div v-for="c in capturesStore.active" :key="c.id" class="notif-capture-item">
                <img :src="c.dataUrl" class="notif-capture-thumb" :alt="`Capture from ${c.label}`" />
                <div class="notif-capture-body">
                  <div class="notif-item-title">Terminal {{ c.label }}</div>
                  <div class="notif-item-time">{{ timeAgo(new Date(c.createdAt).toISOString()) }}</div>
                  <button type="button" class="notif-capture-copy" @click="copyCapture(c.id)">{{ copiedCaptureId === c.id ? '✓ Copied' : '⧉ Copy' }}</button>
                </div>
                <button type="button" class="notif-dismiss" title="Dismiss" @click.stop="capturesStore.remove(c.id)">×</button>
              </div>
            </template>
            <div class="notif-dropdown-header">
              <span>Notifications</span>
              <button type="button" class="notif-mark-all" @click="notifStore.markAllRead()" :disabled="!notifStore.unreadCount">Mark all read</button>
            </div>
            <div v-if="!notifStore.items.length" class="notif-empty">No notifications yet</div>
            <div v-for="n in notifStore.items" :key="n.id" class="notif-item" :class="{ unread: !n.read }" @click="openNotification(n)">
              <span class="notif-dot" :class="'sev-' + n.severity" />
              <div class="notif-item-body">
                <div class="notif-item-title">{{ n.title }}</div>
                <div v-if="n.message" class="notif-item-message">{{ n.message }}</div>
                <div class="notif-item-time">{{ timeAgo(n.created_at) }}</div>
              </div>
              <button type="button" class="notif-dismiss" title="Dismiss" @click.stop="notifStore.dismiss(n.id)">×</button>
            </div>
            <div class="notif-dropdown-footer">
              <!-- Critical (job failures) can never be muted — only info/medium. -->
              <label class="notif-mute-toggle">
                <input type="checkbox" :checked="notifStore.mutedSeverities.includes('info')" @change="toggleMute('info')" />
                Mute info
              </label>
              <label class="notif-mute-toggle">
                <input type="checkbox" :checked="notifStore.mutedSeverities.includes('medium')" @change="toggleMute('medium')" />
                Mute medium
              </label>
            </div>
          </div>
        </div>
        <router-link v-if="canAnySettings" to="/settings" class="topbar-icon-btn" active-class="active" title="Settings">
          <span v-html="ICONS.gear" style="display:flex;align-items:center;" />
        </router-link>
        <button class="topbar-btn" @click="openTerminal" title="SSH Terminal">
          <span v-html="ICONS.terminal" style="display:flex;align-items:center;" />
          SSH Terminal
        </button>
        <a class="topbar-link" :href="zabbixUrl" target="_blank" title="Back to Zabbix">
          <span v-html="ICONS.arrowUpRight" style="display:flex;align-items:center;" />
          Zabbix
        </a>
        <a class="topbar-link" href="https://seyalrun.com/guide/introduction" target="_blank" rel="noopener" title="SeyalRun documentation">
          <span v-html="ICONS.docs" style="display:flex;align-items:center;" />
          Docs
        </a>
      </div>
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useNotificationsStore } from '@/stores/notifications'
import { useCapturesStore } from '@/stores/captures'
import api, { terminalUrl } from '@/api/client'
import { theme, toggleTheme } from '@/theme'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const notifStore = useNotificationsStore()
const capturesStore = useCapturesStore()
const copiedCaptureId = ref<string | null>(null)

async function copyCapture(id: string) {
  const ok = await capturesStore.copy(id)
  if (ok) {
    copiedCaptureId.value = id
    setTimeout(() => { if (copiedCaptureId.value === id) copiedCaptureId.value = null }, 1500)
  }
}

// ── SF Symbol-style SVG icon set (24×24, stroke-based, Apple thin-line aesthetic) ──
function _svg(body: string): string {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`
}
const ICONS = {
  logo:        _svg('<path d="M12 2.25 4.5 5.063v6.562a8.25 8.25 0 007.5 8.25 8.25 8.25 0 007.5-8.25V5.063L12 2.25z"/><path d="m9 12.75 2.25 2.25 3.75-5.25"/>'),
  dashboard:   _svg('<path d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z"/>'),
  hosts:       _svg('<path d="M9 17.25v1.007a3 3 0 01-.879 2.122L7.5 21h9l-.621-.621A3 3 0 0115 18.257V17.25m6-12V15a2.25 2.25 0 01-2.25 2.25H5.25A2.25 2.25 0 013 15V5.25m18 0A2.25 2.25 0 0018.75 3H5.25A2.25 2.25 0 003 5.25m18 0V12a2.25 2.25 0 01-2.25 2.25H5.25A2.25 2.25 0 013 12V5.25"/>'),
  assets:      _svg('<path d="M21 7.5l-9-5.25L3 7.5m18 0-9 5.25m9-5.25v9l-9 5.25M3 7.5l9 5.25M3 7.5v9l9 5.25m0-9v9"/>'),
  sessions:    _svg('<path d="M6.75 7.5l3 2.25-3 2.25m4.5 0h3m-9 8.25h13.5A2.25 2.25 0 0021 18V6a2.25 2.25 0 00-2.25-2.25H5.25A2.25 2.25 0 003 6v12a2.25 2.25 0 002.25 2.25z"/>'),
  automation:  _svg('<path d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z"/>'),
  users:       _svg('<path d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z"/>'),
  lock:        _svg('<path d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z"/>'),
  key:         _svg('<path d="M15.75 5.25a3 3 0 013 3m3 0a6 6 0 01-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.175.659-1.597l6.41-6.409c.404-.404.527-1 .43-1.563A6 6 0 1121.75 8.25z"/>'),
  globe:       _svg('<path d="M12 21a9.004 9.004 0 008.716-6.747M12 21a9.004 9.004 0 01-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S9.515 3 12 3m0 0a8.997 8.997 0 017.843 4.582M12 3a8.997 8.997 0 00-7.843 4.582m15.686 0A11.953 11.953 0 0112 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0121 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0112 16.5c-3.162 0-6.133-.815-8.716-2.247m0 0A9.015 9.015 0 013 12c0-1.605.42-3.113 1.157-4.418"/>'),
  shield:      _svg('<path d="M12 2.25 4.5 5.063v6.562a8.25 8.25 0 007.5 8.25 8.25 8.25 0 007.5-8.25V5.063L12 2.25z"/><path d="m9 12.75 2.25 2.25 3.75-5.25"/>'),
  gear:        _svg('<path d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065zM15 12a3 3 0 11-6 0 3 3 0 016 0z"/>'),
  bell:        _svg('<path d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0"/>'),
  clipboard:   _svg('<path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"/>'),
  terminal:    _svg('<path d="M6.75 7.5l3 2.25-3 2.25m4.5 0h3m-9 8.25h13.5A2.25 2.25 0 0021 18V6a2.25 2.25 0 00-2.25-2.25H5.25A2.25 2.25 0 003 6v12a2.25 2.25 0 002.25 2.25z"/>'),
  docs:        _svg('<path d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25"/>'),
  arrowUpRight: _svg('<path d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25"/>'),
  chevronRight: _svg('<path d="M8.25 4.5l7.5 7.5-7.5 7.5"/>'),
  chevronLeft:  _svg('<path d="M15.75 19.5L8.25 12l7.5-7.5"/>'),
  bolt:        _svg('<path d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z"/>'),
  sun:         _svg('<path d="M12 3v2.25m0 13.5V21m9-9h-2.25M5.25 12H3m15.364-6.364-1.591 1.591M7.227 16.773l-1.591 1.591m0-12.728 1.591 1.591m9.546 9.546 1.591 1.591M16.5 12a4.5 4.5 0 11-9 0 4.5 4.5 0 019 0z"/>'),
  moon:        _svg('<path d="M21.752 15.002A9.72 9.72 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 009.002-5.998z"/>'),
}

// Access/Inventory/Trigger Bindings stay under Admin's own expandable tree. Integration,
// SeyalRun Settings, and the whole former "Platform" group (Health/Security/Housekeeping/
// Log Backend/Audit Logs) moved to the dedicated Settings page (topbar gear icon) —
// AdminView.vue's own nav (used only inside the Zabbix iframe) is untouched and still
// shows all 13 sections there, unchanged.
const ADMIN_GROUPS = [
  { label: 'Access', tabs: [
    { area: 'admin.users', to: '/admin/users', label: 'Users & Groups', icon: ICONS.users },
    { area: 'admin.roles', to: '/admin/roles', label: 'Roles', icon: ICONS.shield },
    { area: 'admin.authorizations', to: '/admin/authorizations', label: 'Authorizations', icon: ICONS.lock },
  ] },
  { label: 'Inventory', tabs: [
    { area: 'admin.credentials', to: '/admin/credentials', label: 'Credentials', icon: ICONS.key },
  ] },
  { label: 'Automation', tabs: [
    { area: 'admin.zabbix-integration', to: '/admin/trigger-bindings', label: 'Trigger Bindings', icon: ICONS.bolt },
  ] },
]

const SETTINGS_AREAS = ['admin.integration', 'admin.platform', 'admin.health', 'admin.security', 'admin.housekeeping', 'admin.log-backend', 'admin.audit']
const canAnySettings = computed(() => SETTINGS_AREAS.some((a) => auth.can(a)))

const onAdminRoute = computed(() => route.path.startsWith('/admin'))
// Manually expandable, not always-open — but start expanded when a direct link/refresh
// already landed on an admin page, so the active item isn't hidden behind an extra click.
const adminExpanded = ref(onAdminRoute.value)

// When loaded inside the Zabbix "Manage SeyalRun" iframe, Zabbix's own
// sidebar already provides navigation — rendering ours on top causes
// the two menus to visually overlap. Detect the iframe and hide ours.
const isEmbedded = window.self !== window.top
const collapsed = ref(false)
// Injected at Docker build time from the release tag (see frontend/Dockerfile);
// empty in local dev, where there's no release version to show.
const appVersion = import.meta.env.VITE_APP_VERSION as string | undefined

// Zabbix link: prefer the URL configured in .env (Admin → Integration); fall back to the
// same-host /zabbix path. Fetched once so the header button matches the Integration page.
const zabbixUrl = ref(`${location.protocol}//${location.host}/zabbix/`)
// True only once Zabbix integration is actually configured (an API URL is set) AND
// the token is either missing or proven invalid — not shown at all during initial
// setup (no URL configured yet), only once there's a real problem to fix.
const zabbixTokenWarning = ref(false)
onMounted(async () => {
  try {
    const { data } = await api.get('/integration/info')
    if (data?.zabbix_url) zabbixUrl.value = data.zabbix_url
    zabbixTokenWarning.value = !!data?.configured && (!data?.token_configured || data?.token_valid === false)
  } catch { /* keep fallback */ }
})

// Opened once here (not per-page) so notifications keep arriving while browsing —
// see stores/notifications.ts connect()'s own doc comment.
onMounted(() => {
  notifStore.load().catch(() => {})
  notifStore.loadPreferences().catch(() => {})
  notifStore.connect()
})
onUnmounted(() => notifStore.disconnect())

const showNotifDropdown = ref(false)
function toggleNotifDropdown() {
  showNotifDropdown.value = !showNotifDropdown.value
  if (showNotifDropdown.value) notifStore.load().catch(() => {})
}
function openNotification(n: { id: string; read: boolean; source_type: string; source_id: string | null }) {
  if (!n.read) notifStore.markRead(n.id)
  showNotifDropdown.value = false
  if (n.source_type === 'job_run' && n.source_id) router.push(`/jobs/${n.source_id}`)
}
function toggleMute(severity: string) {
  const cur = notifStore.mutedSeverities
  const next = cur.includes(severity) ? cur.filter((s) => s !== severity) : [...cur, severity]
  notifStore.setPreferences(next)
}
function timeAgo(iso: string | null): string {
  if (!iso) return ''
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (secs < 60) return 'just now'
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

async function doLogout() {
  await auth.logout()
  router.push('/login')
}

function openTerminal() {
  window.open(terminalUrl(), '_blank')
}
</script>

<style scoped>
/* ── SVG icon sizing ────────────────────────────────────────────────────────── */
.nav-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}
.nav-icon :deep(svg) {
  width: 18px;
  height: 18px;
  display: block;
}

.logo-icon {
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--accent2);
}
.logo-icon :deep(svg) { width: 22px; height: 22px; }

.sidebar-collapse-btn :deep(svg) { width: 14px; height: 14px; }

/* ── Topbar buttons ─────────────────────────────────────────────────────────── */
.topbar-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.topbar-btn:hover {
  background: var(--bg2);
  border-color: var(--accent2);
  color: var(--accent2);
}
.topbar-btn :deep(svg) { width: 15px; height: 15px; }

/* ── Notifications bell + dropdown ─────────────────────────────────────────── */
.notif-wrap { position: relative; }
.notif-badge {
  position: absolute; top: -4px; right: -4px; min-width: 16px; height: 16px; padding: 0 3px;
  border-radius: 999px; background: var(--danger); color: #fff; font-size: 10px; font-weight: 700;
  line-height: 16px; text-align: center; box-shadow: 0 0 0 2px var(--bg2);
}
.notif-backdrop { position: fixed; inset: 0; z-index: 40; }
.notif-dropdown {
  position: absolute; top: calc(100% + 8px); right: 0; z-index: 41; width: 340px; max-height: 420px;
  overflow-y: auto; background: var(--bg2); border: 1px solid var(--border); border-radius: 10px;
  box-shadow: 0 12px 32px rgba(0,0,0,0.35);
}
.notif-dropdown-header {
  display: flex; align-items: center; justify-content: space-between; padding: 10px 14px;
  border-bottom: 1px solid var(--border); font-size: 13px; font-weight: 600; color: var(--text);
  position: sticky; top: 0; background: var(--bg2);
}
.notif-mark-all { background: none; border: none; color: var(--accent2); font-size: 11.5px; cursor: pointer; }
.notif-mark-all:disabled { color: var(--text2); cursor: default; }
.notif-empty { padding: 24px 14px; text-align: center; color: var(--text2); font-size: 12.5px; }
.notif-item { display: flex; align-items: flex-start; gap: 10px; padding: 10px 14px; border-bottom: 1px solid var(--border); cursor: pointer; transition: background 0.12s; }
.notif-item:last-child { border-bottom: none; }
.notif-item:hover { background: var(--bg3); }
.notif-item.unread { background: rgba(59,130,246,0.05); }
.notif-dot { width: 8px; height: 8px; border-radius: 999px; margin-top: 5px; flex-shrink: 0; }
.notif-dot.sev-info { background: var(--accent2); }
.notif-dot.sev-medium { background: var(--warn); }
.notif-dot.sev-critical { background: var(--danger); }
.notif-item-body { flex: 1; min-width: 0; }
.notif-item-title { font-size: 12.5px; font-weight: 600; color: var(--text); }
.notif-item-message { font-size: 11.5px; color: var(--text2); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.notif-item-time { font-size: 10.5px; color: var(--text2); margin-top: 3px; }
.notif-dismiss { background: none; border: none; color: var(--text2); font-size: 16px; line-height: 1; cursor: pointer; padding: 0 2px; flex-shrink: 0; }
.notif-dismiss:hover { color: var(--danger); }
.notif-capture-hint { font-size: 10.5px; font-weight: 400; color: var(--text2); }
.notif-capture-item { display: flex; align-items: center; gap: 10px; padding: 8px 14px; border-bottom: 1px solid var(--border); }
.notif-capture-thumb { width: 56px; height: 40px; object-fit: cover; border-radius: 4px; border: 1px solid var(--border); background: #1a1b1e; flex-shrink: 0; }
.notif-capture-body { flex: 1; min-width: 0; }
.notif-capture-copy { margin-top: 3px; background: none; border: 1px solid var(--border); border-radius: 4px; color: var(--accent2); font-size: 10.5px; padding: 2px 6px; cursor: pointer; }
.notif-capture-copy:hover { border-color: var(--accent2); }
.notif-dropdown-footer {
  display: flex; gap: 14px; padding: 8px 14px; border-top: 1px solid var(--border);
  position: sticky; bottom: 0; background: var(--bg2);
}
.notif-mute-toggle { display: flex; align-items: center; gap: 5px; font-size: 11px; color: var(--text2); cursor: pointer; }
.notif-mute-toggle input { accent-color: #58a6ff; }
</style>
