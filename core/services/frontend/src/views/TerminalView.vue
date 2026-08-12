<template>
  <!-- Standalone fullscreen SSH terminal — opened as a popup window or directly -->
  <div class="ssh-app" @click="closeAll" @keydown.esc.window="closeAll">

    <!-- ── Menu bar ─────────────────────────────────────────────────────────── -->
    <header class="menu-bar" @click.stop>
      <div class="menu-brand">⚡ SeyalRun SSH</div>

      <nav class="menu-nav">
        <div class="menu-group" :class="{ open: activeMenu === 'file' }">
          <button class="menu-btn" @click.stop="toggleMenu('file')">File</button>
          <ul class="menu-dropdown">
            <template v-if="!auth.isKiosk">
              <li @click="newConnection">New Connection</li>
              <li @click="renameActive">Rename Session…</li>
              <li class="m-sep" />
              <li @click="doSplit('h')">Split Horizontal</li>
              <li @click="doSplit('v')">Split Vertical</li>
              <li class="m-sep" />
            </template>
            <li v-if="!auth.isKiosk" :class="{ dim: !activePane?.session }" @click="closeActivePane">Close Pane</li>
            <li v-if="!auth.isKiosk" class="m-sep" />
            <li @click="() => window.close()">Close Window</li>
          </ul>
        </div>

        <div class="menu-group" :class="{ open: activeMenu === 'edit' }">
          <button class="menu-btn" @click.stop="toggleMenu('edit')">Edit</button>
          <ul class="menu-dropdown">
            <li @click="editCopy">Copy</li>
            <li @click="editPaste">Paste</li>
            <li class="m-sep" />
            <li @click="editClear">Clear Screen</li>
            <li class="m-sep" />
            <li @click="editSnip">Capture Screen</li>
          </ul>
        </div>

        <div class="menu-group" :class="{ open: activeMenu === 'view' }">
          <button class="menu-btn" @click.stop="toggleMenu('view')">View</button>
          <ul class="menu-dropdown">
            <li @click="fontSize = Math.min(fontSize + 1, 28)">Zoom In <kbd>Ctrl++</kbd></li>
            <li @click="fontSize = Math.max(fontSize - 1, 8)">Zoom Out <kbd>Ctrl+-</kbd></li>
            <li @click="fontSize = 14">Reset Zoom</li>
            <li class="m-sep" />
            <li class="dim">Theme</li>
            <li v-for="t in themeNames" :key="t" @click="setTheme(t)">{{ themeName === t ? '✓ ' : '  ' }}{{ t }}</li>
            <li class="m-sep" />
            <li @click="toggleFullscreen">{{ isFullscreen ? 'Exit Fullscreen' : 'Fullscreen' }} <kbd>F11</kbd></li>
          </ul>
        </div>

        <div class="menu-group" :class="{ open: activeMenu === 'help' }">
          <button class="menu-btn" @click.stop="toggleMenu('help')">Help</button>
          <ul class="menu-dropdown">
            <li class="dim">Right-click host → Connect as user</li>
            <li class="dim">Ctrl+C/D sends signal to SSH</li>
            <li class="dim">Ctrl++/- adjusts font size</li>
            <li class="m-sep" />
            <li class="dim">SeyalRun v2.0</li>
          </ul>
        </div>
      </nav>

      <div class="menu-status">
        <span v-if="activeSessionCount" class="conn-badge">{{ activeSessionCount }} connected</span>
        <button v-if="!auth.isKiosk" class="menu-capture" :disabled="!focusedPane?.session" @click="editSnip" title="Capture screen (Ctrl/Cmd+S)">📷 Capture</button>
        <button class="menu-close" @click="() => window.close()" title="Close Window">✕</button>
      </div>
    </header>

    <!-- ── Main layout ──────────────────────────────────────────────────────── -->
    <div class="ssh-layout">

      <!-- Left: host panel — hidden entirely in kiosk mode (server-enforced single-host binding) -->
      <aside v-if="!auth.isKiosk" class="host-panel">
        <div class="hp-head">
          <span>Hosts</span>
          <button class="hp-refresh-btn" @click="loadHosts" title="Refresh"><svg style="width:14px;height:14px;display:block" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"/></svg></button>
        </div>
        <div class="hp-search">
          <input v-model="hostFilter" type="text" placeholder="Filter…" class="hp-input" @click.stop />
        </div>
        <ul class="hp-list" @click.stop>
          <!-- SeyalRun native hosts -->
          <template v-if="filteredSeyalRunHosts.length">
            <li class="hp-section">SeyalRun</li>
            <li
              v-for="host in filteredSeyalRunHosts"
              :key="host.id"
              class="hp-item"
              :class="{ 'hp-item--active': hostIsConnected(host) }"
              @click="onHostClick(host)"
              @contextmenu.prevent="onRightClick($event, host)"
            >
              <span class="dot" :class="hostDot(host)" :title="hostDotTitle(host)"></span>
              <div class="hp-item-body">
                <span class="hp-item-name">{{ host.name }}</span>
                <span class="hp-item-ip">{{ host.ip }}</span>
              </div>
            </li>
          </template>

          <li v-if="hostsError" class="hp-error">{{ hostsError }}</li>
          <li v-else-if="!hostsLoading && !filteredSeyalRunHosts.length" class="hp-empty">
            {{ hostFilter ? 'No match' : 'No active hosts' }}
          </li>
        </ul>
      </aside>

      <!-- Right: terminal area -->
      <section class="term-area" @click.stop>
        <!-- Tab bar — hidden in kiosk mode: exactly one pane exists, no tabs/add/split needed -->
        <div v-if="!auth.isKiosk" class="tab-bar">
          <div
            v-for="pane in panes"
            :key="pane.id"
            class="tab"
            :class="{ active: pane.id === activePaneId }"
            @click="activePaneId = pane.id"
            @dblclick="renameTab(pane)"
            title="Double-click to rename this session"
          >
            <span class="dot tab-dot" :class="pane.error ? 'dot-orange' : pane.connecting ? 'dot-blue' : pane.session ? 'dot-green' : 'dot-dim'"></span>
            <span class="tab-label">{{ pane.name || pane.label }}</span>
            <button class="tab-x" @click.stop="closePane(pane.id)" title="Close tab">✕</button>
          </div>
          <button class="tab-add" @click.stop="addPane()" title="New connection">+</button>
          <button v-if="splitId" class="tab-split-badge" @click.stop="exitSplit" title="Exit split">
            {{ splitDir === 'h' ? '⊞ H-split' : '⊟ V-split' }} ✕
          </button>
        </div>

        <!-- Pane area -->
        <div class="pane-area" :class="paneAreaClass">
          <!-- Primary pane -->
          <div
            class="pane"
            :class="{ 'pane--focused': !splitId || focusedSide === 'primary' }"
            @click="focusedSide = 'primary'; termRefs.primary?.focus()"
          >
            <TermSession
              v-if="activePane?.session"
              :key="activePane.session.session_id"
              :session-id="activePane.session.session_id"
              :ws-path="activePane.session.ws_path"
              :font-size="fontSize"
              :theme="currentTheme"
              :ref="(el: any) => { termRefs.primary = el }"
              @disconnected="onDisconnected(activePaneId)"
              @reconnect="onReconnect(activePaneId)"
              @split="doSplit"
              @font-size-delta="adjustFontSize"
            />
            <div v-else class="pane-empty">
              <div class="pane-empty-inner" :class="{ 'pane-empty-error': activePane?.error }">
                <template v-if="activePane?.connecting">
                  <span class="pane-empty-icon"><svg style="width:36px;height:36px;display:block;opacity:0.5" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"/></svg></span>
                  <p>Connecting to {{ activePane.label }}…</p>
                </template>
                <template v-else-if="activePane?.error">
                  <span class="pane-empty-icon pane-error-icon">⚠</span>
                  <p class="pane-error-msg">{{ activePane.error }}</p>
                  <p class="pane-empty-hint">Click the host again to retry</p>
                </template>
                <template v-else>
                  <span class="pane-empty-icon">⚡</span>
                  <p>Click a host to connect</p>
                  <p class="pane-empty-hint">Right-click a host for more options</p>
                </template>
              </div>
            </div>
          </div>

          <!-- Secondary pane (split mode) -->
          <div
            v-if="splitId"
            class="pane"
            :class="{ 'pane--focused': focusedSide === 'secondary' }"
            @click="focusedSide = 'secondary'; termRefs.secondary?.focus()"
          >
            <TermSession
              v-if="splitPane?.session"
              :key="splitPane.session.session_id"
              :session-id="splitPane.session.session_id"
              :ws-path="splitPane.session.ws_path"
              :font-size="fontSize"
              :theme="currentTheme"
              :ref="(el: any) => { termRefs.secondary = el }"
              @disconnected="onDisconnected(splitId!)"
              @reconnect="onReconnect(splitId!)"
              @split="doSplit"
              @font-size-delta="adjustFontSize"
            />
            <div v-else class="pane-empty">
              <div class="pane-empty-inner" :class="{ 'pane-empty-error': splitPane?.error }">
                <template v-if="splitPane?.connecting">
                  <span class="pane-empty-icon"><svg style="width:36px;height:36px;display:block;opacity:0.5" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"/></svg></span>
                  <p>Connecting to {{ splitPane.label }}…</p>
                </template>
                <template v-else-if="splitPane?.error">
                  <span class="pane-empty-icon pane-error-icon">⚠</span>
                  <p class="pane-error-msg">{{ splitPane.error }}</p>
                  <p class="pane-empty-hint">Click the host again to retry</p>
                </template>
                <template v-else>
                  <span class="pane-empty-icon">⚡</span>
                  <p>Click a host to connect here</p>
                </template>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>

    <!-- ── Context menu ─────────────────────────────────────────────────────── -->
    <div
      v-if="ctx.visible"
      class="ctx-menu"
      :style="{ top: ctx.y + 'px', left: ctx.x + 'px' }"
      @click.stop
    >
      <div class="ctx-header">{{ ctx.host?.name }}</div>
      <template v-if="ctx.credentials.length">
        <div class="ctx-section">Connect as</div>
        <button
          v-for="cred in ctx.credentials"
          :key="cred.id"
          class="ctx-item"
          @click="ctxConnectWithCred(cred)"
        >
          ▶ {{ cred.username || cred.name }}
        </button>
      </template>
      <button v-else class="ctx-item" @click="ctxConnectDefault">▶ Connect</button>
      <div class="ctx-sep" />
      <button class="ctx-item" @click="ctxSplitConnect('h')">⊞ Split Horizontal &amp; Connect</button>
      <button class="ctx-item" @click="ctxSplitConnect('v')">⊟ Split Vertical &amp; Connect</button>
      <template v-if="hostIsConnected(ctx.host)">
        <div class="ctx-sep" />
        <button class="ctx-item ctx-danger" @click="ctxDisconnect">✕ Disconnect</button>
      </template>
    </div>

    <!-- ── Credential picker ──────────────────────────────────────────────── -->
    <div v-if="credPicker.visible" class="cred-picker-overlay" @click.self="closePicker">
      <div class="cred-picker">
        <div class="cred-picker-header">
          <span>Connect to {{ credPicker.host?.name }}</span>
          <button class="cred-picker-close" @click="closePicker">✕</button>
        </div>
        <div class="cred-picker-body">
          <p class="cred-picker-hint">Select a credential:</p>
          <button
            v-for="cred in credPicker.credentials"
            :key="cred.id"
            class="cred-picker-item"
            @click="pickCred(cred)"
          >
            ▶ {{ cred.username || cred.name }}
          </button>
        </div>
      </div>
    </div>

    <!-- ── Zabbix deep-link confirmation — never auto-connect without an explicit click ── -->
    <div v-if="zbxConfirm.visible" class="cred-picker-overlay" @click.self="closeZbxConfirm">
      <div class="cred-picker">
        <div class="cred-picker-header">
          <span>Connect to {{ zbxConfirm.host?.name }} ({{ zbxConfirm.host?.ip }})?</span>
          <button class="cred-picker-close" @click="closeZbxConfirm">✕</button>
        </div>
        <div class="cred-picker-body">
          <p class="cred-picker-hint">Requested from Zabbix. Nothing connects until you click below.</p>
          <p style="padding:0 16px 8px;font-size:12px;color:#8b949e">
            Signed into SeyalRun as <strong style="color:#e6edf3">{{ auth.user?.username || '(unknown)' }}</strong> —
            the session will be recorded under this identity. Not you? Log out and back in as yourself first.
          </p>
          <div v-if="zbxConfirm.error" style="padding:8px 16px;color:#f85149;font-size:13px">{{ zbxConfirm.error }}</div>
          <button
            v-for="cred in zbxConfirm.credentials"
            :key="cred.id"
            class="cred-picker-item"
            @click="confirmZbxCred(cred)"
          >
            ▶ Connect as {{ cred.username || cred.name }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, reactive, onMounted, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import TermSession from '@/components/terminal/TermSession.vue'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

// ── Types ────────────────────────────────────────────────────────────────

interface Session { session_id: string; ws_path: string }
interface Pane {
  id: string; label: string; name?: string | null; hostId: string | null
  session: Session | null
  disconnected: boolean    // session ended — show reconnect overlay
  error: string | null     // last connection error to show in pane
  connecting: boolean      // connecting in-progress spinner
}
interface Ctx { visible: boolean; x: number; y: number; host: any; credentials: any[] }

// ── State ─────────────────────────────────────────────────────────────────

const route = useRoute()

const fontSize = ref(14)
function adjustFontSize(delta: number) {
  fontSize.value = Math.min(28, Math.max(8, fontSize.value + delta))
}

// ── Terminal themes ─────────────────────────────────────────────────────────
const THEMES: Record<string, Record<string, string>> = {
  'SeyalRun Dark': {
    background: '#1a1b1e', foreground: '#dce1e7', cursor: '#dce1e7', cursorAccent: '#1a1b1e', selectionBackground: '#3a4a5a',
    black: '#1c1e26', red: '#ff5c57', green: '#5af78e', yellow: '#f3f99d', blue: '#57c7ff', magenta: '#ff6ac1', cyan: '#9aedfe', white: '#f1f1f0',
    brightBlack: '#686868', brightRed: '#ff6e6e', brightGreen: '#69ff94', brightYellow: '#ffffa5', brightBlue: '#d6acff', brightMagenta: '#ff92df', brightCyan: '#a4ffff', brightWhite: '#ffffff',
  },
  'Light': {
    background: '#fafafa', foreground: '#24292f', cursor: '#24292f', cursorAccent: '#fafafa', selectionBackground: '#b9d6f0',
    black: '#24292f', red: '#cf222e', green: '#116329', yellow: '#7d4e00', blue: '#0969da', magenta: '#8250df', cyan: '#1b7c83', white: '#6e7781',
    brightBlack: '#57606a', brightRed: '#a40e26', brightGreen: '#1a7f37', brightYellow: '#633c01', brightBlue: '#218bff', brightMagenta: '#a475f9', brightCyan: '#3192aa', brightWhite: '#24292f',
  },
  'Solarized Dark': {
    background: '#002b36', foreground: '#93a1a1', cursor: '#93a1a1', cursorAccent: '#002b36', selectionBackground: '#073642',
    black: '#073642', red: '#dc322f', green: '#859900', yellow: '#b58900', blue: '#268bd2', magenta: '#d33682', cyan: '#2aa198', white: '#eee8d5',
    brightBlack: '#586e75', brightRed: '#cb4b16', brightGreen: '#586e75', brightYellow: '#657b83', brightBlue: '#839496', brightMagenta: '#6c71c4', brightCyan: '#93a1a1', brightWhite: '#fdf6e3',
  },
  'Dracula': {
    background: '#282a36', foreground: '#f8f8f2', cursor: '#f8f8f2', cursorAccent: '#282a36', selectionBackground: '#44475a',
    black: '#21222c', red: '#ff5555', green: '#50fa7b', yellow: '#f1fa8c', blue: '#bd93f9', magenta: '#ff79c6', cyan: '#8be9fd', white: '#f8f8f2',
    brightBlack: '#6272a4', brightRed: '#ff6e6e', brightGreen: '#69ff94', brightYellow: '#ffffa5', brightBlue: '#d6acff', brightMagenta: '#ff92df', brightCyan: '#a4ffff', brightWhite: '#ffffff',
  },
}
const themeNames = Object.keys(THEMES)
const themeName = ref(localStorage.getItem('sr_term_theme') || 'SeyalRun Dark')
const currentTheme = computed(() => THEMES[themeName.value] || THEMES['SeyalRun Dark'])
function setTheme(name: string) { themeName.value = name; localStorage.setItem('sr_term_theme', name); closeAll() }

// ── Session name (rename a tab, e.g. "migration activity") ──────────────────
function renameTab(pane: Pane) {
  const next = window.prompt('Session name', pane.name || pane.label)
  if (next !== null) pane.name = next.trim() || null
}
function renameActive() { if (activePane.value) renameTab(activePane.value) }
const isFullscreen = ref(false)
const activeMenu = ref<string | null>(null)

// Hosts
const hosts = ref<any[]>([])
const hostsLoading = ref(false)
const hostsError = ref('')
const hostFilter = ref('')
const recentlyDisconnected = reactive(new Set<string>())
const credPicker = reactive<{ visible: boolean; host: any; targetPaneId: string; credentials: any[] }>({
  visible: false, host: null, targetPaneId: '', credentials: []
})
// A link that opens this page and auto-connects (Zabbix deep-link) must never establish
// a live session with zero human awareness — even when there's exactly one authorized
// credential and the normal flow would otherwise skip straight past the picker.
const zbxConfirm = reactive<{ visible: boolean; host: any; targetPaneId: string; credentials: any[]; error: string }>({
  visible: false, host: null, targetPaneId: '', credentials: [], error: ''
})

// Panes
let _pid = 0
const mkPane = (label = 'New'): Pane => ({ id: `p${++_pid}`, label, name: null, hostId: null, session: null, disconnected: false, error: null, connecting: false })
const panes = ref<Pane[]>([mkPane()])
const activePaneId = ref(panes.value[0].id)

// Split
const splitId = ref<string | null>(null)
const splitDir = ref<'h' | 'v'>('h')
const focusedSide = ref<'primary' | 'secondary'>('primary')

// Context menu
const ctx = reactive<Ctx>({ visible: false, x: 0, y: 0, host: null, credentials: [] })

// Term component refs (for copy/paste/clear)
const termRefs = reactive<{ primary: any; secondary: any }>({ primary: null, secondary: null })

// ── Computed ──────────────────────────────────────────────────────────────

const filteredHosts = computed(() => {
  if (!hostFilter.value) return hosts.value
  const q = hostFilter.value.toLowerCase()
  return hosts.value.filter(h => h.name?.toLowerCase().includes(q) || h.ip?.includes(q))
})

const filteredSeyalRunHosts = computed(() =>
  filteredHosts.value.filter(h => !h.zabbix_hostid && h.enabled)
)

const activePane = computed(() => panes.value.find(p => p.id === activePaneId.value) ?? null)
const splitPane  = computed(() => panes.value.find(p => p.id === splitId.value) ?? null)
const focusedPane = computed(() => focusedSide.value === 'secondary' ? splitPane.value : activePane.value)

const activeSessionCount = computed(() => panes.value.filter(p => p.session && !p.disconnected).length)

const paneAreaClass = computed(() => {
  if (!splitId.value) return 'single'
  return splitDir.value === 'h' ? 'split-h' : 'split-v'
})

// ── Menu helpers ──────────────────────────────────────────────────────────

function toggleMenu(name: string) {
  activeMenu.value = activeMenu.value === name ? null : name
}

function closeAll() {
  activeMenu.value = null
  ctx.visible = false
}

// ── Hosts ─────────────────────────────────────────────────────────────────

async function loadHosts() {
  hostsLoading.value = true
  hostsError.value = ''
  try {
    const resp = await api.get('/hosts')
    hosts.value = resp.data
  } catch (e: any) {
    hostsError.value = e.response?.data?.detail ?? 'Failed to load hosts'
  } finally {
    hostsLoading.value = false
  }
}

function hostIsConnected(host: any): boolean {
  if (!host) return false
  return panes.value.some(p => p.hostId === host.id && p.session)
}

function hostDot(host: any): string {
  if (hostIsConnected(host)) return 'dot-green'
  if (recentlyDisconnected.has(host.id)) return 'dot-red'
  return 'dot-dim'
}

function hostDotTitle(host: any): string {
  if (hostIsConnected(host)) return 'SSH connected'
  if (recentlyDisconnected.has(host.id)) return 'Disconnected'
  return 'Not connected'
}

async function connectWithCredPicker(paneId: string, host: any) {
  let creds: any[] = []
  try {
    const resp = await api.get('/ssh/credentials', { params: { host_id: host.id } })
    creds = resp.data ?? []
  } catch { /* fallback: let connectPane handle the error via sessions API */ }

  if (creds.length === 1) {
    await connectPane(paneId, host, creds[0].id)
    return
  }
  if (creds.length > 1) {
    credPicker.host = host
    credPicker.targetPaneId = paneId
    credPicker.credentials = creds
    credPicker.visible = true
    return
  }
  await connectPane(paneId, host)
}

function pickCred(cred: any) {
  const host = credPicker.host
  const paneId = credPicker.targetPaneId
  credPicker.visible = false
  credPicker.host = null
  credPicker.targetPaneId = ''
  credPicker.credentials = []
  if (!host) return
  connectPane(paneId, host, cred.id)
}

// Zabbix deep-link entry point only — always requires an explicit click before a live
// session opens, regardless of how many authorized credentials are available.
async function confirmZbxConnect(paneId: string, host: any) {
  let creds: any[] = []
  let error = ''
  try {
    const resp = await api.get('/ssh/credentials', { params: { host_id: host.id } })
    creds = resp.data ?? []
  } catch (e: any) {
    error = e?.response?.data?.detail || 'Failed to resolve authorized credentials'
  }
  zbxConfirm.host = host
  zbxConfirm.targetPaneId = paneId
  zbxConfirm.credentials = creds
  zbxConfirm.error = !error && creds.length === 0 ? 'You are not authorized to connect to this host.' : error
  zbxConfirm.visible = true
}

function confirmZbxCred(cred: any) {
  const host = zbxConfirm.host
  const paneId = zbxConfirm.targetPaneId
  closeZbxConfirm()
  if (!host) return
  connectPane(paneId, host, cred.id)
}

function closeZbxConfirm() {
  zbxConfirm.visible = false
  zbxConfirm.host = null
  zbxConfirm.targetPaneId = ''
  zbxConfirm.credentials = []
  zbxConfirm.error = ''
}

function closePicker() {
  credPicker.visible = false
  credPicker.host = null
  credPicker.targetPaneId = ''
  credPicker.credentials = []
}

// ── Pane management ───────────────────────────────────────────────────────

function addPane(label?: string): Pane {
  // Single choke point for every "create a pane" path above — kiosk mode never gets
  // a second pane, regardless of which caller reaches here. Returns the existing
  // pane rather than null so callers don't need special-case handling.
  if (auth.isKiosk && panes.value.length >= 1) return panes.value[0]
  const p = mkPane(label)
  panes.value.push(p)
  activePaneId.value = p.id
  return p
}

function closePane(paneId: string) {
  if (splitId.value === paneId) splitId.value = null
  const idx = panes.value.findIndex(p => p.id === paneId)
  if (idx === -1) return
  panes.value.splice(idx, 1)
  if (!panes.value.length) panes.value.push(mkPane())
  if (activePaneId.value === paneId) {
    activePaneId.value = panes.value[Math.min(idx, panes.value.length - 1)].id
  }
}

function closeActivePane() {
  closeAll()
  closePane(activePaneId.value)
}

function newConnection() {
  if (auth.isKiosk) return   // defense in depth — the UI trigger is already hidden
  closeAll()
  addPane()
}

function exitSplit() {
  splitId.value = null
  focusedSide.value = 'primary'
}

function doSplit(dir: 'h' | 'v') {
  if (auth.isKiosk) return   // defense in depth — the UI trigger is already hidden
  closeAll()
  // addPane() always makes the pane it creates the active one (a side effect every
  // other caller wants) — here the new pane is the SPLIT side, not the primary, so
  // that reassignment must be undone or it silently steals the primary slot away
  // from whatever session was already showing there, unmounting it. Confirmed live:
  // this is why clicking Split appeared to "reload" every open session at once —
  // both the primary and secondary slots ended up pointing at the same new, empty pane.
  const originalActiveId = activePaneId.value
  const p = addPane()
  activePaneId.value = originalActiveId
  splitId.value = p.id
  splitDir.value = dir
  focusedSide.value = 'secondary'
}

// ── SSH connect ───────────────────────────────────────────────────────────

async function connectPane(paneId: string, host: any, credentialId?: string) {
  const pane = panes.value.find(p => p.id === paneId)
  if (!pane) return
  pane.error = null
  pane.disconnected = false
  pane.connecting = true
  pane.label = host.name
  pane.session = null
  const body: any = { host_id: host.id }
  if (credentialId) body.credential_id = credentialId
  try {
    const resp = await api.post('/ssh/sessions', body)
    pane.session = { session_id: resp.data.id, ws_path: resp.data.ws_path }
    pane.hostId = host.id
  } catch (e: any) {
    const detail = e.response?.data?.detail ?? ''
    const status  = e.response?.status
    if (status === 403) {
      if (detail.includes('credential')) {
        pane.error = `No credential for "${host.name}" — link one in Admin → Credentials.`
      } else if (detail.includes('ssh action')) {
        pane.error = `SSH not permitted for "${host.name}" — add "ssh" to its Authorization actions.`
      } else if (detail.includes('ACL') || detail.includes('login denied')) {
        pane.error = `Login blocked by ACL for "${host.name}".`
      } else {
        pane.error = `No authorization for "${host.name}" — add one in Admin → Authorizations.`
      }
    } else if (status === 404) {
      pane.error = `Host not found (id ${host.id}).`
    } else if (detail) {
      pane.error = detail
    } else {
      pane.error = `SSH connection failed to "${host.name}" — check the host is reachable and credentials are correct.`
    }
    pane.label = host.name
  } finally {
    pane.connecting = false
  }
}

function targetPaneId(): string {
  // In split mode, clicking a host goes to the focused side's pane
  if (splitId.value && focusedSide.value === 'secondary') return splitId.value
  return activePaneId.value
}

async function onHostClick(host: any) {
  if (auth.isKiosk) return   // defense in depth — the host panel is already hidden
  closeAll()
  const focused = splitId.value && focusedSide.value === 'secondary' ? splitPane.value : activePane.value
  const hasLive = focused?.session && !focused?.disconnected
  const paneId = hasLive ? addPane(host.name).id : targetPaneId()
  await connectWithCredPicker(paneId, host)
}

// ── Context menu ──────────────────────────────────────────────────────────

async function onRightClick(event: MouseEvent, host: any) {
  if (auth.isKiosk) return   // defense in depth — covers every context-menu action below
  closeAll()
  ctx.credentials = []
  const menuW = 260, menuH = 220
  ctx.x = Math.min(event.clientX, window.innerWidth  - menuW)
  ctx.y = Math.min(event.clientY, window.innerHeight - menuH)
  ctx.host = host
  ctx.visible = true

  try {
    const resp = await api.get('/ssh/credentials', { params: { host_id: host.id } })
    if (ctx.host === host) ctx.credentials = resp.data
  } catch { /* fallback: show generic Connect */ }
}

function ctxConnectDefault() {
  const host = ctx.host
  closeAll()
  if (host) connectPane(targetPaneId(), host)
}

function ctxConnectWithCred(cred: any) {
  const host = ctx.host
  closeAll()
  if (host) connectPane(targetPaneId(), host, cred.id)
}

function ctxSplitConnect(dir: 'h' | 'v') {
  const host = ctx.host
  closeAll()
  if (!host) return
  if (!splitId.value) {
    // See doSplit()'s comment — addPane() reassigns activePaneId as a side effect,
    // which must be undone here too or the primary pane's session gets unmounted.
    const originalActiveId = activePaneId.value
    const p = addPane(host.name)
    activePaneId.value = originalActiveId
    splitId.value = p.id
    splitDir.value = dir
    focusedSide.value = 'secondary'
    connectPane(p.id, host)
  } else {
    connectPane(splitId.value, host)
  }
}

function ctxDisconnect() {
  const host = ctx.host
  closeAll()
  if (!host) return
  const pane = panes.value.find(p => p.hostId === host.id && p.session)
  if (pane) {
    pane.session = null
    pane.disconnected = false
    pane.label = 'New'
    pane.hostId = null
  }
}

function onDisconnected(paneId: string) {
  const pane = panes.value.find(p => p.id === paneId)
  if (!pane) return
  if (pane.hostId) {
    const hid = pane.hostId
    recentlyDisconnected.add(hid)
    setTimeout(() => recentlyDisconnected.delete(hid), 5000)
  }
  // Keep pane.session so TermSession stays mounted and shows its reconnect overlay.
  // Set disconnected=true so onHostClick and the session-count badge treat it correctly.
  pane.disconnected = true
  pane.label = 'Disconnected'
}

async function onReconnect(paneId: string) {
  const pane = panes.value.find(p => p.id === paneId)
  if (!pane?.hostId) return
  const host = hosts.value.find(h => h.id === pane.hostId)
  if (!host) return
  // Clear old session so TermSession unmounts cleanly before connectPane remounts it.
  pane.session = null
  pane.disconnected = false
  await connectPane(paneId, host)
}

// ── Edit helpers ──────────────────────────────────────────────────────────

function activeTermRef() {
  return focusedSide.value === 'secondary' ? termRefs.secondary : termRefs.primary
}

function editCopy()  { closeAll(); activeTermRef()?.copySelection?.() }
function editPaste() { closeAll(); activeTermRef()?.pasteText?.() }
function editClear() { closeAll(); activeTermRef()?.clear?.() }
function editSnip()  { closeAll(); activeTermRef()?.snip?.() }

// ── Fullscreen & keyboard ─────────────────────────────────────────────────

function toggleFullscreen() {
  closeAll()
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen().then(() => { isFullscreen.value = true })
  } else {
    document.exitFullscreen().then(() => { isFullscreen.value = false })
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.ctrlKey && (e.key === '=' || e.key === '+')) { fontSize.value = Math.min(fontSize.value + 1, 28); e.preventDefault() }
  if (e.ctrlKey && e.key === '-') { fontSize.value = Math.max(fontSize.value - 1, 8); e.preventDefault() }
  if (e.ctrlKey && e.key === '0') { fontSize.value = 14; e.preventDefault() }
  if (e.key === 'F11') { toggleFullscreen(); e.preventDefault() }
}

// ── Zabbix deep-link / URL param handling ─────────────────────────────────

async function handleUrlParams() {
  const zbxHost   = route.query.zbx_host   as string | undefined
  const hostId    = route.query.host_id    as string | undefined
  const ltToken   = route.query.lt         as string | undefined
  const auto      = route.query.autoconnect === '1' || route.query.autoconnect === 'true'

  let targetZbxHostId: string | null = zbxHost ?? null
  let targetHostId:    string | null = hostId  ?? null

  // Decode link-token payload client-side to extract deep-link params.
  // Server enforces authorization; we just read the hints.
  if (ltToken && !targetZbxHostId && !targetHostId) {
    try {
      const b64 = ltToken.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
      const payload = JSON.parse(atob(b64))
      if (payload.host_id)   targetHostId    = payload.host_id
      if (payload.zbx_hostid) targetZbxHostId = payload.zbx_hostid
    } catch { /* ignore malformed token */ }
  }

  let target: any = null
  if (targetHostId)    target = hosts.value.find(h => h.id             === targetHostId)
  if (!target && targetZbxHostId) target = hosts.value.find(h => h.zabbix_hostid === targetZbxHostId)

  if (target && auto) {
    await confirmZbxConnect(activePaneId.value, target)
  }
}

// ── Lifecycle ─────────────────────────────────────────────────────────────

let _hostsPollTimer: ReturnType<typeof setInterval> | null = null
let _hostsChannel:   BroadcastChannel | null = null

onMounted(async () => {
  document.title = 'SeyalRun SSH Terminal'
  document.addEventListener('keydown', onKeydown)
  await loadHosts()
  await handleUrlParams()

  // Reload host list immediately when Assets page saves a host (same browser, cross-tab)
  _hostsChannel = new BroadcastChannel('seyalrun-hosts')
  _hostsChannel.onmessage = (e) => { if (e.data?.type === 'hosts-updated') loadHosts() }

  // Also poll every 30 s so changes from other browsers/users appear quickly
  _hostsPollTimer = setInterval(loadHosts, 30_000)
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  _hostsChannel?.close()
  if (_hostsPollTimer) clearInterval(_hostsPollTimer)
})
</script>

<style scoped>
/* ── Full-screen app layout ───────────────────────────────────────────────── */
.ssh-app {
  display: flex;
  flex-direction: column;
  width: 100vw;
  height: 100vh;
  background: #1a1b1e;
  color: #dce1e7;
  font-family: system-ui, -apple-system, sans-serif;
  overflow: hidden;
  user-select: none;
}

/* ── Menu bar ─────────────────────────────────────────────────────────────── */
.menu-bar {
  display: flex;
  align-items: center;
  gap: 4px;
  height: 34px;
  padding: 0 12px;
  background: #161b22;
  border-bottom: 1px solid #30363d;
  flex-shrink: 0;
  z-index: 200;
}
.menu-brand {
  font-size: 13px;
  font-weight: 700;
  color: #58a6ff;
  margin-right: 8px;
  white-space: nowrap;
}
.menu-nav { display: flex; align-items: center; gap: 2px; flex: 1; }
.menu-group { position: relative; }
.menu-btn {
  background: none;
  border: none;
  color: #c9d1d9;
  font-size: 13px;
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
}
.menu-btn:hover,
.menu-group.open .menu-btn { background: #21262d; color: #e6edf3; }
.menu-dropdown {
  display: none;
  position: absolute;
  top: calc(100% + 2px);
  left: 0;
  min-width: 200px;
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 6px;
  padding: 4px 0;
  list-style: none;
  margin: 0;
  z-index: 300;
  box-shadow: 0 8px 24px rgba(0,0,0,0.6);
}
.menu-group.open .menu-dropdown { display: block; }
.menu-dropdown li {
  padding: 6px 16px;
  font-size: 13px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  white-space: nowrap;
}
.menu-dropdown li:hover { background: #21262d; }
.menu-dropdown li.dim { color: #8b949e; cursor: default; font-size: 12px; }
.menu-dropdown li.dim:hover { background: none; }
.menu-dropdown li.m-sep {
  height: 1px;
  background: #30363d;
  padding: 0;
  margin: 4px 0;
  pointer-events: none;
}
.menu-dropdown kbd {
  font-size: 11px;
  color: #8b949e;
  background: #21262d;
  border: 1px solid #30363d;
  border-radius: 3px;
  padding: 1px 4px;
}
.menu-status {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
}
.conn-badge {
  font-size: 11px;
  color: #3fb950;
  background: #0d1117;
  padding: 2px 8px;
  border-radius: 10px;
  border: 1px solid #238636;
}
.menu-close {
  background: none;
  border: none;
  color: #8b949e;
  font-size: 14px;
  padding: 4px 8px;
  cursor: pointer;
  border-radius: 4px;
}
.menu-close:hover { background: #da3633; color: #fff; }
.menu-capture {
  background: #161b22;
  border: 1px solid #30363d;
  color: #dce1e7;
  font-size: 12px;
  padding: 4px 10px;
  cursor: pointer;
  border-radius: 5px;
}
.menu-capture:hover:not(:disabled) { border-color: #58a6ff; color: #58a6ff; }
.menu-capture:disabled { opacity: 0.4; cursor: not-allowed; }

/* ── Main layout ──────────────────────────────────────────────────────────── */
.ssh-layout {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* ── Host panel ───────────────────────────────────────────────────────────── */
.host-panel {
  width: 220px;
  min-width: 180px;
  display: flex;
  flex-direction: column;
  background: #0d1117;
  border-right: 1px solid #21262d;
  flex-shrink: 0;
}
.hp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.8px;
  color: #8b949e;
  border-bottom: 1px solid #21262d;
}
.hp-refresh-btn {
  background: none;
  border: none;
  color: #8b949e;
  font-size: 16px;
  cursor: pointer;
  padding: 0 2px;
  border-radius: 3px;
}
.hp-refresh-btn { display:flex; align-items:center; justify-content:center; }
.hp-refresh-btn:hover { color: #58a6ff; }
.hp-search { padding: 6px 8px; border-bottom: 1px solid #21262d; }
.hp-input {
  width: 100%;
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 5px;
  color: #e6edf3;
  font-size: 12px;
  padding: 4px 8px;
  box-sizing: border-box;
  outline: none;
}
.hp-input:focus { border-color: #58a6ff; }
.hp-list {
  flex: 1;
  overflow-y: auto;
  list-style: none;
  margin: 0;
  padding: 4px 0;
}
.hp-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  cursor: pointer;
  border-radius: 4px;
  margin: 1px 4px;
  transition: background 0.1s;
}
.hp-item:hover { background: #161b22; }
.hp-item--active { background: #161b22; }
.hp-item-body { display: flex; flex-direction: column; min-width: 0; flex: 1; }
.hp-item-name { font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hp-item-ip { font-size: 11px; color: #8b949e; font-family: monospace; }
.hp-empty, .hp-error {
  text-align: center;
  padding: 16px;
  font-size: 12px;
  color: #8b949e;
}
.hp-error { color: #f85149; }
.hp-section {
  padding: 10px 12px 3px;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.8px;
  color: #6e7681;
  list-style: none;
  user-select: none;
}
.hp-section:not(:first-child) { border-top: 1px solid #21262d; margin-top: 4px; }
/* ── Status dots ──────────────────────────────────────────────────────────── */
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  transition: background 0.3s;
}
.dot-green  { background: #3fb950; box-shadow: 0 0 5px #3fb95066; }
.dot-red    { background: #f85149; box-shadow: 0 0 5px #f8514966; }
.dot-orange { background: #f0883e; box-shadow: 0 0 5px #f0883e66; }
.dot-blue   { background: #58a6ff; animation: pulse 1s infinite; }
.dot-dim    { background: #30363d; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

/* ── Terminal area ────────────────────────────────────────────────────────── */
.term-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ── Tab bar ──────────────────────────────────────────────────────────────── */
.tab-bar {
  display: flex;
  align-items: center;
  height: 32px;
  background: #161b22;
  border-bottom: 1px solid #30363d;
  flex-shrink: 0;
  overflow-x: auto;
  overflow-y: hidden;
}
.tab-bar::-webkit-scrollbar { height: 3px; }
.tab-bar::-webkit-scrollbar-thumb { background: #30363d; border-radius: 2px; }
.tab {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 0 12px;
  height: 100%;
  cursor: pointer;
  border-right: 1px solid #21262d;
  font-size: 12px;
  white-space: nowrap;
  color: #8b949e;
  transition: background 0.1s;
  flex-shrink: 0;
}
.tab:hover { background: #21262d; color: #c9d1d9; }
.tab.active { background: #0d1117; color: #e6edf3; }
.tab-dot { width: 6px; height: 6px; }
.tab-label { max-width: 120px; overflow: hidden; text-overflow: ellipsis; }
.tab-x {
  background: none;
  border: none;
  color: #8b949e;
  font-size: 11px;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 3px;
  opacity: 0;
}
.tab:hover .tab-x,
.tab.active .tab-x { opacity: 1; }
.tab-x:hover { background: #da3633; color: #fff; }
.tab-add {
  background: none;
  border: none;
  color: #8b949e;
  font-size: 18px;
  padding: 0 12px;
  cursor: pointer;
  height: 100%;
  flex-shrink: 0;
}
.tab-add:hover { color: #58a6ff; background: #21262d; }
.tab-split-badge {
  margin-left: auto;
  background: #1c2128;
  border: 1px solid #30363d;
  border-radius: 4px;
  color: #8b949e;
  font-size: 11px;
  padding: 2px 8px;
  cursor: pointer;
  margin-right: 8px;
  flex-shrink: 0;
}
.tab-split-badge:hover { color: #f85149; border-color: #f85149; }

/* ── Pane area ────────────────────────────────────────────────────────────── */
.pane-area {
  flex: 1;
  display: flex;
  overflow: hidden;
}
.pane-area.single { }
.pane-area.split-h { flex-direction: row; }
.pane-area.split-v { flex-direction: column; }

.pane {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
  border: 2px solid transparent;
  transition: border-color 0.15s;
}
.pane + .pane { border-left: 1px solid #21262d; }
.pane-area.split-v .pane + .pane { border-left: none; border-top: 1px solid #21262d; }
.pane--focused { border-color: #1f6feb; }

/* ── Empty pane ───────────────────────────────────────────────────────────── */
.pane-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #1a1b1e;
}
.pane-empty-inner { text-align: center; color: #8b949e; }
.pane-empty-icon { font-size: 40px; display: block; margin-bottom: 12px; opacity: 0.5; }
.pane-empty-inner p { margin: 4px 0; font-size: 14px; }
.pane-empty-hint  { font-size: 12px; color: #6e7681; }
.pane-empty-error { }
.pane-error-icon  { color: #f0883e !important; opacity: 1 !important; font-size: 32px !important; }
.pane-error-msg   { color: #f0883e; font-size: 13px; max-width: 380px; text-align: center; line-height: 1.5; }

/* ── Context menu ─────────────────────────────────────────────────────────── */
.ctx-menu {
  position: fixed;
  z-index: 1000;
  min-width: 240px;
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 6px;
  padding: 4px 0;
  box-shadow: 0 8px 24px rgba(0,0,0,0.7);
  font-size: 13px;
}
.ctx-header {
  padding: 6px 14px 4px;
  font-weight: 700;
  color: #58a6ff;
  font-size: 12px;
  border-bottom: 1px solid #21262d;
  margin-bottom: 4px;
}
.ctx-section {
  padding: 4px 14px 2px;
  font-size: 11px;
  color: #8b949e;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.6px;
}
.ctx-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 14px;
  background: none;
  border: none;
  color: #c9d1d9;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}
.ctx-item:hover { background: #21262d; color: #e6edf3; }
.ctx-danger { color: #f85149; }
.ctx-danger:hover { background: #21262d; color: #ff7b72; }
.ctx-sep {
  height: 1px;
  background: #21262d;
  margin: 4px 0;
}

/* ── Scrollbars ───────────────────────────────────────────────────────────── */
.hp-list::-webkit-scrollbar { width: 4px; }
.hp-list::-webkit-scrollbar-track { background: transparent; }
.hp-list::-webkit-scrollbar-thumb { background: #30363d; border-radius: 2px; }

/* ── Credential picker ───────────────────────────────────────────────────── */
.cred-picker-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.6);
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding: 24px;
  z-index: 500;
}
.cred-picker {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 14px;
  width: min(420px, 94vw);
  max-height: calc(100vh - 48px);
  overflow-y: auto;
  box-shadow: 0 16px 50px rgba(0,0,0,0.6);
  animation: cred-picker-in 0.2s ease;
}
@keyframes cred-picker-in { from { transform: translateX(28px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
@media (max-width: 560px) {
  .cred-picker-overlay { padding: 0; }
  .cred-picker { width: 100vw; max-height: 100vh; height: 100vh; border-radius: 0; border: none; }
}
.cred-picker-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  font-size: 13px;
  font-weight: 700;
  color: #58a6ff;
  border-bottom: 1px solid #21262d;
}
.cred-picker-close {
  background: none;
  border: none;
  color: #8b949e;
  font-size: 14px;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
}
.cred-picker-close:hover { background: #da3633; color: #fff; }
.cred-picker-body { padding: 8px 0 12px; }
.cred-picker-hint {
  padding: 4px 16px 8px;
  font-size: 12px;
  color: #8b949e;
  margin: 0;
}
.cred-picker-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 16px;
  background: none;
  border: none;
  color: #c9d1d9;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: background 0.1s;
}
.cred-picker-item:hover { background: #21262d; color: #e6edf3; }
</style>
