<template>
  <div class="workspace" @click.self="closeContextMenu">

    <!-- ── Left sidebar ─────────────────────────────────────────── -->
    <aside class="left-sidebar" :class="{ collapsed: sidebarCollapsed }">
      <!-- SeyalRun brand -->
      <div class="brand" @click="sidebarCollapsed = !sidebarCollapsed">
        <span class="brand-icon">⬡</span>
        <span v-if="!sidebarCollapsed" class="brand-name">SeyalRun</span>
      </div>

      <!-- Home link — opens in new tab so terminal sessions stay alive -->
      <a v-if="!sidebarCollapsed" href="/dashboard" target="_blank" rel="noopener" class="home-link" title="Go to Dashboard (opens new tab)">
        ⌂ Dashboard ↗
      </a>
      <a v-else href="/dashboard" target="_blank" rel="noopener" class="home-link-icon" title="Go to Dashboard (opens new tab)">
        ⌂
      </a>

      <template v-if="!sidebarCollapsed">
        <!-- Search -->
        <div class="sidebar-search">
          <input v-model="assetSearch" class="search-input" placeholder="Search hosts…" />
        </div>

        <!-- Asset list -->
        <div class="asset-list">
          <div v-if="assetsLoading" class="sidebar-loading">Loading…</div>
          <div v-else-if="filteredAssets.length === 0" class="sidebar-empty">No hosts</div>

          <div
            v-for="a in filteredAssets"
            :key="a.id"
            class="asset-item"
            :class="{ connected: isConnected(a.address) }"
            @click="promptConnect(a)"
            @contextmenu.prevent="onAssetRightClick($event, a)"
          >
            <span class="asset-dot" :class="a.is_active ? 'dot-ok' : 'dot-off'" />
            <div class="asset-text">
              <div class="asset-name">{{ a.name }}</div>
              <div class="asset-ip mono">{{ a.address }}</div>
            </div>
            <button
              class="connect-btn"
              @click.stop="promptConnect(a)"
              title="Connect"
            >⊙</button>
          </div>
        </div>

        <!-- Active sessions count -->
        <div class="sidebar-footer">
          <span class="footer-stat">{{ activeTabs.length }} session{{ activeTabs.length !== 1 ? 's' : '' }}</span>
          <router-link to="/sessions" class="footer-link">History →</router-link>
        </div>
      </template>

      <!-- Collapsed icon strip -->
      <template v-else>
        <div class="icon-strip">
          <div
            v-for="a in filteredAssets.slice(0,8)"
            :key="a.id"
            class="icon-asset"
            :title="a.name"
            @click="promptConnect(a)"
          >
            <span class="asset-dot" :class="a.is_active ? 'dot-ok' : 'dot-off'" />
          </div>
        </div>
      </template>
    </aside>

    <!-- ── Main terminal area ────────────────────────────────────── -->
    <div class="main-area">

      <!-- Tab bar -->
      <div class="tab-bar">
        <div
          v-for="tab in tabs"
          :key="tab.id"
          class="tab"
          :class="{ 'tab-active': activeTabId === tab.id }"
          @click="activeTabId = tab.id"
          @contextmenu.prevent="onTabRightClick($event, tab.id)"
        >
          <span class="tab-dot" :class="`dot-${tab.status}`" />
          <span class="tab-label">{{ tab.label }}</span>
          <button
            class="tab-close"
            @click.stop="closeTab(tab.id)"
            :title="tab.panes.length > 1 ? `Close all ${tab.panes.length} sessions in this tab` : 'Close session'"
          >✕</button>
        </div>

        <button class="tab-new" @click="promptNewTab" title="New session (Ctrl+Shift+T)">＋</button>

        <div class="tab-spacer" />

        <!-- Split controls -->
        <div class="split-controls">
          <button
            class="split-btn"
            :class="{ 'split-active': activeTab?.layout === 'single' }"
            @click="setSplit('single')"
            title="Single pane"
          >▣</button>
          <button
            class="split-btn"
            :class="{ 'split-active': activeTab?.layout === 'vsplit' }"
            @click="setSplit('vsplit')"
            title="Split vertical (side by side)"
          >⊟</button>
          <button
            class="split-btn"
            :class="{ 'split-active': activeTab?.layout === 'hsplit' }"
            @click="setSplit('hsplit')"
            title="Split horizontal (top/bottom)"
          >⊞</button>
        </div>
      </div>

      <!-- Pane area (v-show so xterm stays mounted) -->
      <div class="pane-area" :class="activeTab?.layout || 'single'">
        <template v-for="tab in tabs" :key="tab.id">
          <template v-for="(pane, idx) in tab.panes" :key="pane.id">
            <TerminalPane
              v-show="activeTabId === tab.id"
              :ref="el => setPaneRef(pane.id, el)"
              :session-id="pane.sessionId"
              :token="auth.token"
              :label="pane.label"
              :active="activeTabId === tab.id && activePaneIdx === idx"
              :closable="isPaneClosable(tab, idx)"
              :is-primary="idx === 0"
              :style="getPaneStyle(tab, idx)"
              @activate="activatePaneInTab(tab.id, idx)"
              @close="removePaneFromTab(tab.id, idx)"
              @close-blocked="onCloseBlocked(tab.id)"
              @reconnect="reconnectPane(tab.id, idx)"
              @state-change="(s) => onPaneStateChange(tab.id, idx, s)"
              @right-click="(x,y) => onPaneRightClick(x, y, tab.id, idx)"
            />
          </template>
        </template>

        <!-- Empty state when no tabs -->
        <div v-if="tabs.length === 0" class="empty-workspace">
          <div class="empty-icon">⊙</div>
          <p>No active sessions</p>
          <p class="text-muted">Select a host from the left sidebar or click ＋ to start</p>
          <button class="empty-btn" @click="promptNewTab">⊙ New SSH Session</button>
        </div>
      </div>
    </div>

    <!-- ── Context menu ───────────────────────────────────────────── -->
    <div
      v-if="ctxMenu.show"
      class="ctx-menu"
      :style="{ left: ctxMenu.x + 'px', top: ctxMenu.y + 'px' }"
      @click.stop
    >
      <div
        v-for="item in ctxMenu.items"
        :key="item.label"
        class="ctx-item"
        :class="{ 'ctx-separator': item.separator, 'ctx-danger': item.danger }"
        @click="() => { item.action?.(); closeContextMenu() }"
      >
        <span v-if="!item.separator">{{ item.icon }} {{ item.label }}</span>
        <hr v-else />
      </div>
    </div>

    <!-- ── Quick Connect Modal ────────────────────────────────────── -->
    <div v-if="qcModal.show" class="modal-overlay" @click.self="qcModal.show = false">
      <div class="qc-modal">
        <div class="qc-header">
          <div>
            <h3>⊙ SSH Connect</h3>
            <p class="mono text-muted">{{ qcModal.host }}</p>
          </div>
          <button @click="qcModal.show = false">✕</button>
        </div>
        <div class="qc-body">
          <!-- JMS accounts -->
          <div v-if="qcModal.accounts.length" class="acc-chips">
            <button
              v-for="acc in qcModal.accounts"
              :key="acc.username"
              class="acc-chip"
              :class="{ active: qcModal.username === acc.username }"
              @click="qcModal.username = acc.username"
            >{{ acc.privileged ? '⚡' : '👤' }} {{ acc.username }}</button>
          </div>
          <div class="qc-fields">
            <div class="qc-field">
              <label>Username</label>
              <input v-model="qcModal.username" class="qc-input mono" placeholder="test" @keydown.enter="doConnect" autofocus />
            </div>
            <div class="qc-field">
              <label>Password</label>
              <input v-model="qcModal.password" type="password" class="qc-input" placeholder="••••" @keydown.enter="doConnect" />
            </div>
          </div>
          <label class="qc-check">
            <input type="checkbox" v-model="qcModal.openInNewTab" />
            Open in new tab
          </label>
        </div>
        <div class="qc-footer">
          <button class="btn-cancel" @click="qcModal.show = false">Cancel</button>
          <button class="btn-connect" :disabled="qcModal.connecting || !qcModal.username" @click="doConnect">
            {{ qcModal.connecting ? '⊙ Connecting…' : '⊙ Connect' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, reactive, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import TerminalPane from '@/components/ssh/TerminalPane.vue'
import { api } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const ui = useUiStore()

// ── Types ──────────────────────────────────────────────────────
interface Pane { id: string; sessionId: string; label: string }
interface Tab {
  id: string
  label: string
  status: 'connecting' | 'connected' | 'closed' | 'error'
  layout: 'single' | 'vsplit' | 'hsplit'
  panes: Pane[]
  activePaneIdx: number
}
interface Asset { id: string; name: string; address: string; is_active: boolean }

// ── State ──────────────────────────────────────────────────────
const tabs = ref<Tab[]>([])
const activeTabId = ref('')
const activePaneIdx = ref(0)
const paneRefs = ref<Record<string, any>>({})

const assets = ref<Asset[]>([])
const assetsLoading = ref(true)
const assetSearch = ref('')
const sidebarCollapsed = ref(false)

const activeTab = computed(() => tabs.value.find(t => t.id === activeTabId.value))
const activeTabs = computed(() => tabs.value.filter(t => t.status !== 'closed'))
const filteredAssets = computed(() => {
  const q = assetSearch.value.toLowerCase()
  if (!q) return assets.value
  return assets.value.filter(a => a.name.toLowerCase().includes(q) || a.address.includes(q))
})

function isConnected(address: string) {
  return tabs.value.some(t =>
    t.panes.some(p => p.label.includes(address)) && t.status === 'connected'
  )
}

// ── Pane refs ──────────────────────────────────────────────────
function setPaneRef(paneId: string, el: any) {
  if (el) paneRefs.value[paneId] = el
  else delete paneRefs.value[paneId]
}

function getPaneStyle(tab: Tab, idx: number) {
  if (tab.layout === 'single') return {}
  if (tab.layout === 'vsplit') return {}
  if (tab.layout === 'hsplit') return {}
  return {}
}

// Primary pane (idx=0) is locked while sub-panes exist
function isPaneClosable(tab: Tab, idx: number): boolean {
  if (idx === 0 && tab.panes.length > 1) return false
  return true
}

function onCloseBlocked(tabId: string) {
  const tab = tabs.value.find(t => t.id === tabId)
  const subCount = (tab?.panes.length ?? 1) - 1
  ui.warn(`Close the ${subCount} sub-session${subCount > 1 ? 's' : ''} first, or click the tab ✕ to close all at once.`)
}

// ── Tab management ─────────────────────────────────────────────
function makePaneId() { return Math.random().toString(36).slice(2, 10) }
function makeTabId() { return Math.random().toString(36).slice(2, 10) }

async function openSession(
  host: string,
  username: string,
  password: string,
  assetId = '',
  assetName = '',
  inNewTab = false,
  gateway?: any,
): Promise<string | null> {
  try {
    const resp = await api.post('/ssh/sessions', {
      asset_id: assetId,
      asset_name: assetName || host,
      asset_address: host,
      ssh_username: username,
      ssh_password: password,
      gateway,
    })
    const sessionId = resp.data.session_id
    const label = `${username}@${host}`

    const pane: Pane = { id: makePaneId(), sessionId, label }

    if (inNewTab || tabs.value.length === 0) {
      // New tab
      const tab: Tab = {
        id: makeTabId(),
        label,
        status: 'connecting',
        layout: 'single',
        panes: [pane],
        activePaneIdx: 0,
      }
      tabs.value.push(tab)
      activeTabId.value = tab.id
      activePaneIdx.value = 0
    } else {
      // Add to active tab as split pane
      const tab = activeTab.value!
      if (tab.panes.length >= 2) {
        // Tab full, open new tab
        const newTab: Tab = {
          id: makeTabId(),
          label,
          status: 'connecting',
          layout: 'single',
          panes: [pane],
          activePaneIdx: 0,
        }
        tabs.value.push(newTab)
        activeTabId.value = newTab.id
        activePaneIdx.value = 0
      } else {
        tab.panes.push(pane)
        tab.layout = 'vsplit'
        tab.activePaneIdx = tab.panes.length - 1
        activePaneIdx.value = tab.panes.length - 1
      }
    }

    return sessionId
  } catch (e: unknown) {
    const axErr = e as { response?: { data?: { detail?: string } } }
    ui.error(axErr?.response?.data?.detail || 'Connection failed')
    return null
  }
}

function closeTab(tabId: string) {
  const idx = tabs.value.findIndex(t => t.id === tabId)
  if (idx === -1) return
  // Terminate sessions
  const tab = tabs.value[idx]
  tab.panes.forEach(p => {
    api.delete(`/ssh/sessions/${p.sessionId}`).catch(() => {})
  })
  tabs.value.splice(idx, 1)
  if (activeTabId.value === tabId) {
    activeTabId.value = tabs.value[Math.max(0, idx - 1)]?.id ?? ''
  }
}

function removePaneFromTab(tabId: string, paneIdx: number) {
  const tab = tabs.value.find(t => t.id === tabId)
  if (!tab) return

  // Guard: cannot close primary pane while sub-panes exist
  if (paneIdx === 0 && tab.panes.length > 1) {
    onCloseBlocked(tabId)
    return
  }

  const pane = tab.panes[paneIdx]
  if (pane) api.delete(`/ssh/sessions/${pane.sessionId}`).catch(() => {})
  tab.panes.splice(paneIdx, 1)

  if (tab.panes.length === 0) {
    closeTab(tabId)
  } else {
    tab.layout = 'single'
    tab.activePaneIdx = 0
    activePaneIdx.value = 0
  }
}

function setSplit(layout: 'single' | 'vsplit' | 'hsplit') {
  const tab = activeTab.value
  if (!tab) return
  if (layout === 'single' && tab.panes.length > 1) {
    // Close extra panes
    tab.panes.slice(1).forEach(p => {
      api.delete(`/ssh/sessions/${p.sessionId}`).catch(() => {})
    })
    tab.panes = tab.panes.slice(0, 1)
  }
  tab.layout = layout
  if (layout !== 'single' && tab.panes.length < 2) {
    qcModal.targetTabForSplit = tab.id
    qcModal.show = true
    qcModal.openInNewTab = false
  }
}

function activatePaneInTab(tabId: string, idx: number) {
  activeTabId.value = tabId
  activePaneIdx.value = idx
  const tab = tabs.value.find(t => t.id === tabId)
  if (tab) tab.activePaneIdx = idx
  nextTick(() => {
    const tab = tabs.value.find(t => t.id === tabId)
    if (tab) {
      const paneId = tab.panes[idx]?.id
      if (paneId) paneRefs.value[paneId]?.focus()
    }
  })
}

async function reconnectPane(tabId: string, paneIdx: number) {
  const tab = tabs.value.find(t => t.id === tabId)
  if (!tab) return
  const oldPane = tab.panes[paneIdx]
  if (!oldPane) return

  // Extract host from label (username@host)
  const parts = oldPane.label.split('@')
  const host = parts.slice(1).join('@')
  promptConnect({ id: '', name: host, address: host, is_active: true })
}

function onPaneStateChange(tabId: string, paneIdx: number, state: string) {
  const tab = tabs.value.find(t => t.id === tabId)
  if (!tab) return
  // Update tab status based on primary pane
  if (paneIdx === 0) {
    tab.status = state as Tab['status']
  }
}

// ── Quick connect modal ────────────────────────────────────────
const qcModal = reactive({
  show: false,
  host: '',
  assetId: '',
  assetName: '',
  username: '',
  password: '', // pragma: allowlist secret
  accounts: [] as { username: string; privileged: boolean }[],
  connecting: false,
  openInNewTab: true,
  targetTabForSplit: '',
  gateway: null as any,
})

async function promptConnect(asset: Asset, forSplit = false) {
  qcModal.host = asset.address
  qcModal.assetId = asset.id
  qcModal.assetName = asset.name
  qcModal.username = 'test'
  qcModal.password = 'test'
  qcModal.accounts = []
  qcModal.openInNewTab = !forSplit && tabs.value.length > 0
  qcModal.targetTabForSplit = forSplit ? activeTabId.value : ''
  qcModal.show = true

  // Load JMS accounts
  try {
    const resp = await api.get(`/assets/${asset.id}/accounts`)
    qcModal.accounts = resp.data.accounts || []
    if (qcModal.accounts.length) {
      const priv = qcModal.accounts.find(a => a.privileged) || qcModal.accounts[0]
      qcModal.username = priv.username
    }
  } catch { /* ignore */ }
}

function promptNewTab() {
  if (assets.value.length > 0) {
    promptConnect(assets.value[0], false)
    qcModal.openInNewTab = true
  } else {
    qcModal.host = ''
    qcModal.username = ''
    qcModal.password = ''
    qcModal.openInNewTab = true
    qcModal.show = true
  }
}

async function doConnect() {
  if (!qcModal.username || qcModal.connecting) return
  qcModal.connecting = true
  try {
    await openSession(
      qcModal.host,
      qcModal.username,
      qcModal.password,
      qcModal.assetId,
      qcModal.assetName,
      qcModal.openInNewTab,
      qcModal.gateway,
    )
    qcModal.show = false
  } finally {
    qcModal.connecting = false
  }
}

// ── Context menus ─────────────────────────────────────────────
interface CtxItem { label?: string; icon?: string; action?: () => void; separator?: boolean; danger?: boolean }
const ctxMenu = reactive({ show: false, x: 0, y: 0, items: [] as CtxItem[] })

function showCtx(x: number, y: number, items: CtxItem[]) {
  // Clamp to viewport
  const mx = Math.min(x, window.innerWidth - 180)
  const my = Math.min(y, window.innerHeight - items.length * 32 - 8)
  ctxMenu.x = mx; ctxMenu.y = my; ctxMenu.items = items; ctxMenu.show = true
}
function closeContextMenu() { ctxMenu.show = false }

function onAssetRightClick(e: MouseEvent, asset: Asset) {
  showCtx(e.clientX, e.clientY, [
    { icon: '⊙', label: 'Connect', action: () => promptConnect(asset) },
    { icon: '⊟', label: 'Connect in Split', action: () => {
      if (activeTab.value && activeTab.value.panes.length < 2) {
        promptConnect(asset, true)
        qcModal.openInNewTab = false
      } else {
        promptConnect(asset, false)
        qcModal.openInNewTab = true
      }
    }},
    { icon: '⊞', label: 'Open in New Tab', action: () => { promptConnect(asset); qcModal.openInNewTab = true } },
    { separator: true },
    { icon: '⎘', label: 'Copy IP', action: () => { navigator.clipboard.writeText(asset.address); ui.toast(`Copied ${asset.address}`) } },
  ])
}

function onTabRightClick(e: MouseEvent, tabId: string) {
  showCtx(e.clientX, e.clientY, [
    { icon: '✕', label: 'Close Tab', danger: true, action: () => closeTab(tabId) },
  ])
}

function onPaneRightClick(x: number, y: number, tabId: string, paneIdx: number) {
  const tab = tabs.value.find(t => t.id === tabId)
  const isMainLocked = paneIdx === 0 && (tab?.panes.length ?? 1) > 1
  showCtx(x, y, [
    { icon: '⊟', label: 'Split Vertical (side by side)', action: () => setSplit('vsplit') },
    { icon: '⊞', label: 'Split Horizontal (top/bottom)', action: () => setSplit('hsplit') },
    { separator: true },
    isMainLocked
      ? { icon: '🔒', label: 'Close Sub-sessions First', action: () => onCloseBlocked(tabId) }
      : { icon: '✕', label: 'Close Pane', danger: true, action: () => removePaneFromTab(tabId, paneIdx) },
    { icon: '✕✕', label: 'Close All in Tab', danger: true, action: () => closeTab(tabId) },
  ])
}

// ── Keyboard shortcuts ─────────────────────────────────────────
function onKeyDown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    // Close dialogs top-down: context menu first, then modal
    if (ctxMenu.show) { closeContextMenu(); return }
    if (qcModal.show) { qcModal.show = false; return }
    return
  }
  if (e.ctrlKey && e.shiftKey) {
    if (e.key === 'T') { e.preventDefault(); promptNewTab() }
    if (e.key === 'W') { e.preventDefault(); if (activeTabId.value) closeTab(activeTabId.value) }
    if (e.key === 'D') { e.preventDefault(); setSplit(activeTab.value?.layout === 'vsplit' ? 'single' : 'vsplit') }
    if (e.key === 'ArrowRight') {
      e.preventDefault()
      const idx = tabs.value.findIndex(t => t.id === activeTabId.value)
      if (idx < tabs.value.length - 1) activeTabId.value = tabs.value[idx + 1].id
    }
    if (e.key === 'ArrowLeft') {
      e.preventDefault()
      const idx = tabs.value.findIndex(t => t.id === activeTabId.value)
      if (idx > 0) activeTabId.value = tabs.value[idx - 1].id
    }
  }
}

// ── Init ───────────────────────────────────────────────────────
onMounted(async () => {
  document.addEventListener('keydown', onKeyDown)
  document.addEventListener('click', closeContextMenu)

  // Load assets
  try {
    const resp = await api.get('/assets')
    assets.value = resp.data.items || []
  } finally {
    assetsLoading.value = false
  }

  // If launched from Assets page with a session ID, open it
  const initSessionId = route.query.session as string
  if (initSessionId) {
    try {
      const resp = await api.get(`/ssh/sessions/${initSessionId}`)
      const sess = resp.data
      const pane: Pane = {
        id: makePaneId(),
        sessionId: initSessionId,
        label: `${sess.ssh_username}@${sess.asset_address}`,
      }
      const tab: Tab = {
        id: makeTabId(),
        label: pane.label,
        status: 'connecting',
        layout: 'single',
        panes: [pane],
        activePaneIdx: 0,
      }
      tabs.value.push(tab)
      activeTabId.value = tab.id
    } catch { /* ignore */ }
  }
})

onUnmounted(() => {
  document.removeEventListener('keydown', onKeyDown)
  document.removeEventListener('click', closeContextMenu)
})
</script>

<style scoped>
/* ── Workspace layout ──────────────────────────────────────────── */
.workspace {
  display: flex;
  height: 100vh;
  background: #010409;
  overflow: hidden;
  position: fixed;
  inset: 0;
}

/* ── Left sidebar ─────────────────────────────────────────────── */
.left-sidebar {
  width: 220px;
  min-width: 220px;
  background: var(--bg-surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  transition: width 200ms, min-width 200ms;
  flex-shrink: 0;
  overflow: hidden;
}
.left-sidebar.collapsed { width: 48px; min-width: 48px; }

.brand {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  user-select: none;
  flex-shrink: 0;
}
.brand-icon { font-size: 18px; color: var(--accent); flex-shrink: 0; }
.brand-name { font-size: 14px; font-weight: 700; color: var(--text); white-space: nowrap; }

.home-link {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-4);
  font-size: 11px;
  color: var(--text-subtle);
  text-decoration: none;
  transition: color var(--transition);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.home-link:hover { color: var(--accent); }

.home-link-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-2);
  font-size: 14px;
  color: var(--text-subtle);
  text-decoration: none;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  transition: color var(--transition);
}
.home-link-icon:hover { color: var(--accent); }

.sidebar-search { padding: var(--space-2) var(--space-3); flex-shrink: 0; }
.search-input {
  width: 100%;
  background: var(--bg-base);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  color: var(--text);
  font-size: 12px;
  padding: var(--space-1) var(--space-2);
  outline: none;
}
.search-input:focus { border-color: var(--accent); }

.asset-list { flex: 1; overflow-y: auto; padding: var(--space-1); }
.sidebar-loading, .sidebar-empty { padding: var(--space-4); font-size: 12px; color: var(--text-subtle); text-align: center; }

.asset-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background var(--transition);
  position: relative;
}
.asset-item:hover { background: var(--bg-overlay); }
.asset-item:hover .connect-btn { opacity: 1; }
.asset-item.connected { background: var(--success-dim); }

.asset-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.dot-ok { background: var(--success); box-shadow: 0 0 4px var(--success); }
.dot-off { background: var(--text-subtle); }
.dot-connecting { background: var(--warning); }
.dot-connected { background: var(--success); }
.dot-closed { background: var(--text-subtle); }
.dot-error { background: var(--error); }

.asset-text { flex: 1; min-width: 0; overflow: hidden; }
.asset-name { font-size: 12px; font-weight: 500; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.asset-ip { font-size: 10px; color: var(--text-muted); font-family: var(--font-mono); }

.connect-btn {
  opacity: 0;
  background: var(--accent-dim);
  border: 1px solid var(--accent);
  color: var(--accent);
  border-radius: var(--radius-sm);
  padding: 1px 6px;
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
  flex-shrink: 0;
  transition: all var(--transition);
}
.connect-btn:hover { background: var(--accent); color: #000; }

.sidebar-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-2) var(--space-3);
  border-top: 1px solid var(--border);
  font-size: 11px;
  flex-shrink: 0;
}
.footer-stat { color: var(--text-subtle); }
.footer-link { color: var(--accent); text-decoration: none; font-size: 11px; }

.icon-strip { padding: var(--space-2) var(--space-2); display: flex; flex-direction: column; gap: var(--space-2); }
.icon-asset { width: 28px; height: 28px; display: flex; align-items: center; justify-content: center; cursor: pointer; border-radius: var(--radius-md); transition: background var(--transition); }
.icon-asset:hover { background: var(--bg-overlay); }

/* ── Main area ───────────────────────────────────────────────── */
.main-area { flex: 1; display: flex; flex-direction: column; min-width: 0; overflow: hidden; }

/* ── Tab bar ─────────────────────────────────────────────────── */
.tab-bar {
  display: flex;
  align-items: center;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border);
  height: 38px;
  flex-shrink: 0;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: none;
}
.tab-bar::-webkit-scrollbar { display: none; }

.tab {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 0 var(--space-3);
  height: 100%;
  min-width: 120px;
  max-width: 200px;
  border-right: 1px solid var(--border);
  cursor: pointer;
  transition: background var(--transition);
  position: relative;
  flex-shrink: 0;
  user-select: none;
}
.tab:hover { background: var(--bg-overlay); }
.tab-active { background: #010409; border-bottom: 2px solid var(--accent); }
.tab-active:hover { background: #010409; }

.tab-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.tab-label { font-size: 12px; font-family: var(--font-mono); color: var(--text-muted); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tab-active .tab-label { color: var(--text); }
.tab-close { background: none; border: none; color: var(--text-subtle); cursor: pointer; font-size: 11px; padding: 2px 4px; border-radius: 3px; flex-shrink: 0; opacity: 0; }
.tab:hover .tab-close { opacity: 1; }
.tab-close:hover { color: var(--error); background: var(--error-dim); }

.tab-new {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 16px;
  padding: 0 var(--space-3);
  height: 100%;
  transition: color var(--transition);
  flex-shrink: 0;
}
.tab-new:hover { color: var(--accent); }

.tab-spacer { flex: 1; }

.split-controls { display: flex; align-items: center; gap: 2px; padding: 0 var(--space-3); border-left: 1px solid var(--border); height: 100%; }
.split-btn {
  background: none; border: 1px solid transparent;
  color: var(--text-muted); cursor: pointer;
  font-size: 14px; padding: 3px 7px; border-radius: var(--radius-md);
  transition: all var(--transition);
}
.split-btn:hover { color: var(--text); border-color: var(--border); }
.split-active { color: var(--accent); border-color: var(--accent); background: var(--accent-dim); }

/* ── Pane area ───────────────────────────────────────────────── */
.pane-area {
  flex: 1;
  display: grid;
  gap: 4px;
  padding: 4px;
  min-height: 0;
  overflow: hidden;
}
.pane-area.single { grid-template-columns: 1fr; grid-template-rows: 1fr; }
.pane-area.vsplit { grid-template-columns: 1fr 1fr; grid-template-rows: 1fr; }
.pane-area.hsplit { grid-template-columns: 1fr; grid-template-rows: 1fr 1fr; }

.empty-workspace {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-4);
  color: var(--text-muted);
  font-size: 14px;
  grid-column: 1/-1;
  grid-row: 1/-1;
}
.empty-icon { font-size: 48px; color: var(--border-muted); }
.text-muted { color: var(--text-muted); font-size: 12px; }
.empty-btn {
  background: var(--success-dim); color: var(--success);
  border: 1px solid var(--success); border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-5); font-size: 13px; font-weight: 700; cursor: pointer;
  transition: all var(--transition);
}
.empty-btn:hover { background: var(--success); color: #000; }

/* ── Context menu ────────────────────────────────────────────── */
.ctx-menu {
  position: fixed;
  background: var(--bg-surface);
  border: 1px solid var(--border-muted);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  padding: var(--space-1);
  z-index: 9000;
  min-width: 160px;
}
.ctx-item { border-radius: var(--radius-sm); cursor: pointer; }
.ctx-item:not(.ctx-separator) { padding: var(--space-2) var(--space-3); font-size: 12px; color: var(--text-muted); display: flex; align-items: center; gap: var(--space-2); transition: all var(--transition); }
.ctx-item:not(.ctx-separator):hover { background: var(--bg-overlay); color: var(--text); }
.ctx-item.ctx-danger:hover { color: var(--error); background: var(--error-dim); }
.ctx-item.ctx-separator { padding: 2px 0; }
.ctx-item.ctx-separator hr { border: none; border-top: 1px solid var(--border); margin: 0; }

/* ── Quick Connect modal ─────────────────────────────────────── */
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.7); display: flex; align-items: center; justify-content: center; z-index: 8000; }
.qc-modal { background: var(--bg-surface); border: 1px solid var(--border-muted); border-radius: var(--radius-xl); width: 420px; box-shadow: var(--shadow-lg); overflow: hidden; }
.qc-header { display: flex; align-items: flex-start; justify-content: space-between; padding: var(--space-4) var(--space-5); border-bottom: 1px solid var(--border); }
.qc-header h3 { font-size: 14px; font-weight: 700; color: var(--success); }
.qc-header button { background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 16px; }
.qc-body { padding: var(--space-4) var(--space-5); display: flex; flex-direction: column; gap: var(--space-3); }
.acc-chips { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.acc-chip { padding: 2px var(--space-3); border: 1px solid var(--border); border-radius: var(--radius-full); background: var(--bg-overlay); color: var(--text-muted); font-size: 11px; font-family: var(--font-mono); cursor: pointer; transition: all var(--transition); }
.acc-chip.active { border-color: var(--success); color: var(--success); background: var(--success-dim); }
.qc-fields { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-3); }
.qc-field { display: flex; flex-direction: column; gap: 4px; }
.qc-field label { font-size: 10px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; }
.qc-input { background: var(--bg-base); border: 1px solid var(--border); border-radius: var(--radius-md); color: var(--text); font-size: 13px; padding: var(--space-2) var(--space-3); outline: none; width: 100%; }
.qc-input.mono { font-family: var(--font-mono); }
.qc-input:focus { border-color: var(--accent); }
.qc-check { display: flex; align-items: center; gap: var(--space-2); font-size: 12px; color: var(--text-muted); cursor: pointer; }
.qc-footer { display: flex; justify-content: flex-end; gap: var(--space-2); padding: var(--space-3) var(--space-5); border-top: 1px solid var(--border); }
.btn-cancel { background: none; border: 1px solid var(--border); color: var(--text-muted); border-radius: var(--radius-md); padding: var(--space-2) var(--space-4); font-size: 12px; cursor: pointer; }
.btn-connect { background: var(--success); color: #000; border: none; border-radius: var(--radius-md); padding: var(--space-2) var(--space-5); font-size: 12px; font-weight: 700; cursor: pointer; }
.btn-connect:disabled { opacity: 0.5; cursor: not-allowed; }

.mono { font-family: var(--font-mono); }
.text-muted { color: var(--text-muted); }
</style>
