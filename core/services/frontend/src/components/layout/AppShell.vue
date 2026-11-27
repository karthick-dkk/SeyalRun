<template>
  <div class="app-shell">
    <!-- Sidebar — hidden when embedded inside Zabbix (its native menu provides navigation) -->
    <nav v-if="!isEmbedded" class="sr-side" aria-label="Main">
      <router-link to="/" class="sr-brand" title="Go to Dashboard">
        <span class="sr-logo"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 8l4 4-4 4M13 16h5" /></svg></span>
        <span class="sr-brand-t"><b>SeyalRun</b><span>Privileged access</span></span>
      </router-link>

      <template v-for="g in navGroups" :key="g.label">
        <div v-if="g.items.length" class="grp">{{ g.label }}</div>
        <router-link v-for="it in g.items" :key="it.key" :to="it.to" class="nav" :class="{ on: isActive(it) }" :aria-current="isActive(it) ? 'page' : undefined">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path v-for="(d, i) in it.paths" :key="i" :d="d" /></svg>
          {{ it.label }}
          <span v-if="it.hot" class="cnt hot" style="margin-left:auto">{{ it.hot }}</span>
          <span v-else-if="it.tag" class="tag sr-aitag">{{ it.tag }}</span>
        </router-link>
      </template>

      <div style="flex:1 1 auto;min-height:16px" />
      <router-link v-if="canAnySettings" to="/settings" class="nav" :class="{ on: isActive({ key: 'settings', match: ['/settings'], exclude: ['/settings/health', '/settings/security', '/settings/audit'] }) }">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9L7 7M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1" /></svg>Settings
      </router-link>
      <router-link v-if="auth.can('admin.health')" to="/settings/health" class="nav" :class="{ on: route.path === '/settings/health' }">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12h4l2-5 4 10 2-5h6" /></svg>System health
      </router-link>
      <div class="sr-ver"><span>{{ appVersion ? 'v' + appVersion : '' }}</span><a href="https://seyalrun.com/guide/introduction" target="_blank" rel="noopener">Docs</a></div>
    </nav>

    <!-- Main -->
    <div class="main-content">
      <header v-if="!isEmbedded" class="sr-top">
        <div class="sr-crumb">
          <span class="muted">{{ crumb[0] }}</span>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="color:var(--faint)"><path d="M9 6l6 6-6 6" /></svg>
          <b>{{ crumb[1] }}</b>
        </div>
        <form class="srch sr-search" role="search" @submit.prevent="doSearch">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
          <label for="g-search" class="vh">Search hosts</label>
          <input id="g-search" ref="searchEl" v-model="searchQ" class="in" placeholder="Search hosts…" autocomplete="off" style="padding-right:52px" />
          <span class="mono sr-kbd" aria-hidden="true">Ctrl K</span>
        </form>
        <div class="acts" style="gap:6px;margin-left:auto">
          <component :is="canIntegration ? 'router-link' : 'span'" v-if="zabbixDown" :to="canIntegration ? '/settings/integration' : undefined"
            class="btn ib g" style="position:relative" aria-label="Zabbix: not connected" title="Zabbix: not connected">
            <span aria-hidden="true" class="sr-z">Z</span><span class="sr-reddot" />
          </component>
          <button type="button" class="btn ib g" @click="toggleTheme" :title="theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'" :aria-label="theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'">
            <span class="sr-ico" v-html="theme === 'dark' ? ICONS.sun : ICONS.moon" />
          </button>
          <button v-if="auth.can('terminal')" type="button" class="btn ib g" @click="openTerminal" aria-label="Open terminal" title="Open terminal">
            <span class="sr-ico" v-html="ICONS.terminal" />
          </button>
          <div class="notif-wrap">
            <button type="button" class="btn ib g" @click="toggleNotifDropdown" :aria-label="`Notifications${notifStore.unreadCount ? ', ' + notifStore.unreadCount + ' unread' : ''}`" :aria-expanded="showNotifDropdown" style="position:relative">
              <span class="sr-ico" v-html="ICONS.bell" />
              <span v-if="notifStore.unreadCount" class="sr-reddot" style="top:5px;right:6px" />
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
                  <button type="button" class="notif-dismiss" title="Dismiss" aria-label="Dismiss capture" @click.stop="capturesStore.remove(c.id)">×</button>
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
                <button type="button" class="notif-dismiss" title="Dismiss" aria-label="Dismiss notification" @click.stop="notifStore.dismiss(n.id)">×</button>
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
          <router-link to="/security" class="sr-acct" :aria-label="`Account: ${displayName}`" title="Profile and security">
            <span class="av sr-av">{{ initials }}</span>
            <span class="sr-acct-n">{{ displayName }}</span>
          </router-link>
          <button type="button" class="btn ib g" @click="doLogout" aria-label="Sign out" title="Sign out">
            <span class="sr-ico" v-html="ICONS.logout" />
          </button>
        </div>
      </header>
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
import { SETTINGS_AREAS, SECTIONS } from '@/config/adminSections'
import { pendingRuns, pendingAuthz, refreshApprovals } from '@/composables/useApprovals'

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
  terminal:    _svg('<path d="M6.75 7.5l3 2.25-3 2.25m4.5 0h3m-9 8.25h13.5A2.25 2.25 0 0021 18V6a2.25 2.25 0 00-2.25-2.25H5.25A2.25 2.25 0 003 6v12a2.25 2.25 0 002.25 2.25z"/>'),
  bell:        _svg('<path d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0"/>'),
  sun:         _svg('<path d="M12 3v2.25m0 13.5V21m9-9h-2.25M5.25 12H3m15.364-6.364-1.591 1.591M7.227 16.773l-1.591 1.591m0-12.728 1.591 1.591m9.546 9.546 1.591 1.591M16.5 12a4.5 4.5 0 11-9 0 4.5 4.5 0 019 0z"/>'),
  moon:        _svg('<path d="M21.752 15.002A9.72 9.72 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 009.002-5.998z"/>'),
  logout:      _svg('<path d="M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15m3 0l3-3m0 0l-3-3m3 3H9"/>'),
}

// ── Sidebar model (Sidebar.dc.html): Overview / Access / Automation / Administration ──
// Runs, Schedules and Remediation are tabs of Playbooks now, so /jobs/:id and
// /automation both light up "Playbooks". Admin items keep their RBAC area checks.
interface NavItem { key: string; label: string; to: string; paths: string[]; match: string[]; exclude?: string[]; hot?: number; tag?: string }
const P = {
  dashboard: ['M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z'],
  approvals: ['M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z', 'M8.5 12l2.5 2.5 4.5-5'],
  hosts: ['M4.5 4h15v7h-15zM4.5 13h15v7h-15z', 'M7 7.5h.01M7 16.5h.01'],
  assets: ['M21 7.5l-9-5.25L3 7.5m18 0-9 5.25m9-5.25v9l-9 5.25M3 7.5l9 5.25M3 7.5v9l9 5.25m0-9v9'],
  sessions: ['M5 4h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z', 'M7 9l3 3-3 3M13 15h4'],
  posture: ['M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6z', 'M8 13l2.5 2.5L16 10'],
  patching: ['M12 3v12m0 0l-4-4m4 4l4-4M5 19h14'],
  playbooks: ['M4 4.5A1.5 1.5 0 0 1 5.5 3H20v15H5.5A1.5 1.5 0 0 0 4 19.5z', 'M4 19.5A1.5 1.5 0 0 0 5.5 21H20v-3'],
  mcp: ['M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z'],
  users: ['M9 4.5a3.5 3.5 0 1 0 0 7a3.5 3.5 0 1 0 0-7z', 'M2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6 6 0 0 1 3.5 6'],
  roles: ['M5 5h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z', 'M9 9.5a2.5 2.5 0 1 0 0 5a2.5 2.5 0 1 0 0-5z', 'M14 10h4M14 14h3'],
  access: ['M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6z', 'M9 12l2 2 4-4'],
  credentials: ['M8 11a4 4 0 1 0 0 8a4 4 0 1 0 0-8z', 'M10.8 12.2L20 3M16 7l3 3M14 9l2 2'],
  zones: ['M12 2.5a2.5 2.5 0 1 0 0 5a2.5 2.5 0 1 0 0-5z', 'M5 16.5a2.5 2.5 0 1 0 0 5a2.5 2.5 0 1 0 0-5z', 'M19 16.5a2.5 2.5 0 1 0 0 5a2.5 2.5 0 1 0 0-5z', 'M12 7.5v4l-6 5.5M12 11.5l6 5.5'],
  policy: ['M4 6h16M7 12h10M10 18h4'],
  audit: ['M6 3h9l4 4v14H6z', 'M9 12h7M9 16h7M9 8h3'],
}
const approvalsHot = computed(() => pendingRuns.value + pendingAuthz.value)
const navGroups = computed<{ label: string; items: NavItem[] }[]>(() => {
  const g = (label: string, items: (NavItem | false)[]) => ({ label, items: items.filter(Boolean) as NavItem[] })
  return [
    g('Overview', [
      auth.can('dashboard') && { key: 'dashboard', label: 'Dashboard', to: '/', paths: P.dashboard, match: ['/'] },
      auth.can('jobs') && { key: 'approvals', label: 'Approvals', to: '/approvals', paths: P.approvals, match: ['/approvals'], hot: approvalsHot.value || undefined },
    ]),
    g('Access', [
      auth.can('hosts') && { key: 'hosts', label: 'Hosts', to: '/hosts', paths: P.hosts, match: ['/hosts'] },
      auth.can('assets') && { key: 'assets', label: 'Assets', to: '/assets', paths: P.assets, match: ['/assets'] },
      auth.can('sessions') && { key: 'sessions', label: 'Sessions', to: '/sessions', paths: P.sessions, match: ['/sessions'] },
      auth.can('posture') && { key: 'posture', label: 'Posture', to: '/posture', paths: P.posture, match: ['/posture'] },
      auth.can('posture') && { key: 'patching', label: 'Patching', to: '/patching', paths: P.patching, match: ['/patching'] },
    ]),
    g('Automation', [
      auth.can('automation') && { key: 'playbooks', label: 'Playbooks', to: '/automation', paths: P.playbooks, match: ['/automation', '/jobs'] },
      auth.can('mcp') && { key: 'mcp', label: 'MCP', to: '/mcp', paths: P.mcp, match: ['/mcp'], tag: 'AI' },
    ]),
    g('Administration', [
      auth.can('admin.users') && { key: 'users', label: 'Users & groups', to: '/admin/users', paths: P.users, match: ['/admin/users'] },
      auth.can('admin.roles') && { key: 'roles', label: 'Roles', to: '/admin/roles', paths: P.roles, match: ['/admin/roles'] },
      auth.can('admin.authorizations') && { key: 'access', label: 'Access rules', to: '/admin/authorizations', paths: P.access, match: ['/admin/authorizations'] },
      auth.can('admin.authorizations') && { key: 'host-tree', label: 'Host tree', to: '/admin/host-tree', paths: P.assets, match: ['/admin/host-tree'] },
      auth.can('admin.credentials') && { key: 'credentials', label: 'Credentials', to: '/admin/credentials', paths: P.credentials, match: ['/admin/credentials'] },
      auth.can('admin.zones') && { key: 'zones', label: 'Zones & gateways', to: '/admin/zones', paths: P.zones, match: ['/admin/zones'] },
      auth.can('admin.security') && { key: 'policy', label: 'Command policy', to: '/settings/security', paths: P.policy, match: ['/settings/security'] },
      auth.can('admin.audit') && { key: 'audit', label: 'Audit log', to: '/settings/audit', paths: P.audit, match: ['/settings/audit'] },
    ]),
  ]
})
function isActive(it: { key: string; match: string[]; exclude?: string[] }): boolean {
  const p = route.path
  if (it.exclude?.some((x) => p === x || p.startsWith(x + '/'))) return false
  return it.match.some((m) => (m === '/' ? p === '/' : p === m || p.startsWith(m + '/')))
}
const canAnySettings = computed(() => SETTINGS_AREAS.some((a) => auth.can(a)))
const canIntegration = computed(() => auth.can('admin.integration'))

// ── Topbar ────────────────────────────────────────────────────────────────
const crumb = computed<[string, string]>(() => {
  const p = route.path
  if (p === '/') return ['Overview', 'Dashboard']
  if (p.startsWith('/approvals')) return ['Overview', 'Approvals']
  if (p.startsWith('/hosts')) return ['Access', 'Hosts']
  if (p.startsWith('/assets')) return ['Access', 'Assets']
  if (p.startsWith('/sessions')) return ['Access', 'Sessions']
  if (p.startsWith('/posture')) return ['Access', 'Posture']
  if (p.startsWith('/patching')) return ['Access', 'Patching']
  if (p.startsWith('/terminal')) return ['Access', 'Terminal']
  if (p.startsWith('/jobs/')) return ['Playbooks', 'Run']
  if (p.startsWith('/automation')) return ['Automation', 'Playbooks']
  if (p.startsWith('/zbx')) return ['Automation', 'Run from Zabbix']
  if (p.startsWith('/mcp')) return ['Automation', 'MCP']
  if (p.startsWith('/security')) return ['Account', 'Profile']
  const m = p.match(/^\/(admin|settings)\/([^/]+)/)
  if (m) {
    const sec = SECTIONS.find((x) => x.slug === m[2])
    return [m[1] === 'admin' ? 'Administration' : 'Settings', sec?.label || 'Overview']
  }
  return ['SeyalRun', String(route.meta.title || '')]
})
const displayName = computed(() => auth.user?.display_name || auth.user?.username || 'Account')
const initials = computed(() => {
  const parts = displayName.value.split(/[^A-Za-z0-9]+/).filter(Boolean)
  const t = parts.length > 1 ? parts[0][0] + parts[1][0] : (parts[0] || '?').slice(0, 2)
  return t.toUpperCase()
})
const searchQ = ref('')
const searchEl = ref<HTMLInputElement | null>(null)
function doSearch() {
  const q = searchQ.value.trim()
  router.push({ path: '/hosts', query: q ? { q } : {} })
}
function onKey(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); searchEl.value?.focus() }
}
onMounted(() => { window.addEventListener('keydown', onKey); refreshApprovals(auth.isAdmin) })
onUnmounted(() => window.removeEventListener('keydown', onKey))

// When loaded inside the Zabbix "Manage SeyalRun" iframe, Zabbix's own
// sidebar already provides navigation — rendering ours on top causes
// the two menus to visually overlap. Detect the iframe and hide ours.
const isEmbedded = window.self !== window.top
// Injected at Docker build time from the release tag (see frontend/Dockerfile);
// empty in local dev, where there's no release version to show.
const appVersion = import.meta.env.VITE_APP_VERSION as string | undefined

// Zabbix link: prefer the URL configured in .env (Admin → Integration); fall back to the
// same-host /zabbix path. Fetched once so the header button matches the Integration page.
const zabbixUrl = ref(`${location.protocol}//${location.host}/zabbix/`)
// True only once Zabbix integration is actually configured (an API URL is set) AND
// the token is either missing or proven invalid — not shown at all during initial
// setup (no URL configured yet), only once there's a real problem to fix.
const zabbixDown = ref(false)
onMounted(async () => {
  try {
    const { data } = await api.get('/integration/info')
    if (data?.zabbix_url) zabbixUrl.value = data.zabbix_url
    // Connected = an API URL is configured AND the token is present and not proven invalid.
    zabbixDown.value = !data?.configured || !data?.token_configured || data?.token_valid === false
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
/* ── Sidebar (Sidebar.dc.html) ─────────────────────────────────────────────── */
.sr-side { flex: 0 0 248px; width: 248px; background: var(--surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; padding: 14px 10px 12px; gap: 1px; overflow-y: auto; }
.sr-brand { display: flex; align-items: center; gap: 10px; padding: 4px 8px 12px; text-decoration: none; color: var(--text); }
.sr-logo { width: 30px; height: 30px; border-radius: 8px; background: var(--primary); display: inline-flex; align-items: center; justify-content: center; color: #fff; }
.sr-brand-t { display: flex; flex-direction: column; line-height: 1.2; }
.sr-brand-t b { font-size: 15px; letter-spacing: -.01em; }
.sr-brand-t span { font-size: 11.5px; color: var(--faint); }
.grp { font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: var(--faint); padding: 16px 10px 6px; font-weight: 600; }
.nav { display: flex; align-items: center; gap: 10px; height: 34px; padding: 0 10px; border-radius: 7px; text-decoration: none; font-size: 13.5px; font-weight: 500; color: var(--muted); }
.nav:hover { background: var(--hover); color: var(--text); }
.nav.on { background: var(--hover); color: var(--text); }
.nav.on svg { color: var(--link); }
.nav svg { flex: none; }
.sr-aitag { margin-left: auto; height: 18px; font-size: 10.5px; padding: 0 6px; color: var(--primary); border-color: color-mix(in srgb, var(--primary) 40%, var(--border)); }
.sr-ver { border-top: 1px solid var(--border); margin-top: 10px; padding: 10px 10px 0; font-size: 11.5px; color: var(--faint); display: flex; justify-content: space-between; }
.sr-ver a { color: var(--faint); }
@media (max-width: 860px) { .sr-side { flex-basis: 64px; width: 64px; } .sr-brand-t, .grp, .sr-ver, .nav { font-size: 0; } .nav { justify-content: center; padding: 0; } .nav .cnt, .nav .tag { display: none; } }

/* ── Topbar (Topbar.dc.html) ───────────────────────────────────────────────── */
.sr-top { min-height: 60px; display: flex; align-items: center; gap: 14px; padding: 10px 28px; border-bottom: 1px solid var(--border); background: var(--surface); flex-wrap: wrap; flex-shrink: 0; }
.sr-crumb { display: flex; align-items: center; gap: 8px; font-size: 13.5px; min-width: 0; }
.sr-crumb b { font-weight: 600; }
.sr-search { flex: 1 1 260px; max-width: 440px; margin-left: 12px; }
.sr-kbd { position: absolute; right: 8px; font-size: 11px; color: var(--faint); border: 1px solid var(--border2); border-radius: 4px; padding: 1px 5px; pointer-events: none; }
.sr-z { width: 20px; height: 20px; border-radius: 4px; background: #D40000; color: #fff; display: inline-flex; align-items: center; justify-content: center; font: 700 13px/1 var(--font); }
.sr-reddot { position: absolute; top: 3px; right: 3px; width: 10px; height: 10px; border-radius: 50%; background: var(--bad); border: 2px solid var(--surface); }
.sr-ico { display: inline-flex; width: 17px; height: 17px; }
.sr-ico :deep(svg) { width: 17px; height: 17px; display: block; }
.sr-acct { display: flex; align-items: center; gap: 8px; text-decoration: none; color: var(--text); padding: 3px 10px 3px 3px; border-radius: 999px; border: 1px solid var(--border); }
.sr-acct:hover { background: var(--hover); }
.sr-av { background: var(--primary); color: #fff; }
.sr-acct-n { font-size: 13px; font-weight: 500; max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 720px) { .sr-top { padding: 10px 16px; } .sr-acct-n { display: none; } }

/* ── Notifications bell + dropdown ─────────────────────────────────────────── */
.notif-wrap { position: relative; }
.notif-badge {
  position: absolute; top: -4px; right: -4px; min-width: 16px; height: 16px; padding: 0 3px;
  border-radius: 999px; background: var(--danger); color: #fff; font-size: 10px; font-weight: 700;
  line-height: 16px; text-align: center; box-shadow: 0 0 0 2px var(--bg2);
}
.notif-backdrop { position: fixed; inset: 0; z-index: 40; }
.notif-dropdown {
  position: absolute; top: calc(100% + 8px); right: 0; z-index: 41; width: 340px; max-height: 420px; z-index: 41;
  overflow-y: auto; background: var(--bg2); border: 1px solid var(--border); border-radius: 10px;
  box-shadow: var(--shadow);
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
.notif-item.unread { background: color-mix(in srgb, var(--primary) 7%, transparent); }
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
.notif-mute-toggle input { accent-color: var(--primary); }
</style>
