<template>
  <!-- Standalone fullscreen SSH terminal — opened as a popup window or directly -->
  <div class="ssh-app" @click="closeAll">

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
            <li class="m-sep" />
            <li @click="showWatermark = !showWatermark">{{ showWatermark ? '✓ ' : '  ' }}Session Watermark</li>
          </ul>
        </div>

        <div class="menu-group" :class="{ open: activeMenu === 'help' }">
          <button class="menu-btn" @click.stop="toggleMenu('help')">Help</button>
          <ul class="menu-dropdown">
            <li @click="showShortcuts = true">Keyboard Shortcuts…</li>
            <li class="m-sep" />
            <li class="dim">Right-click host → Connect as user</li>
            <li class="dim">Ctrl+C/D sends signal to SSH</li>
            <li class="m-sep" />
            <li class="dim">SeyalRun v2.0</li>
          </ul>
        </div>
      </nav>

      <div class="menu-status">
        <span v-if="activeSessionCount" class="conn-badge">{{ activeSessionCount }} connected</span>
        <button
          class="menu-capture"
          :class="{ 'is-on': showFiles }"
          :disabled="!focusedPane?.session"
          @click="toggleFiles"
          :title="focusedPane?.session ? 'File manager (SFTP)' : 'Connect a session to browse files'"
        ><svg class="icon-inline" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l1.5 2h9.5A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z"/></svg> Files</button>
        <button v-if="!auth.isKiosk" class="menu-capture" :disabled="!focusedPane?.session" @click="editSnip" title="Capture screen (Ctrl/Cmd+S)"><svg class="icon-inline" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 8.5A1.5 1.5 0 0 1 4.5 7h2L8 5h8l1.5 2h2A1.5 1.5 0 0 1 21 8.5v9A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z"/><circle cx="12" cy="12.5" r="3.2"/></svg> Capture</button>
        <button class="menu-close" @click="() => window.close()" title="Close Window">✕</button>
      </div>
    </header>

    <!-- ── Main layout ──────────────────────────────────────────────────────── -->
    <div class="ssh-layout">

      <!-- Left: host panel — hidden entirely in kiosk mode (server-enforced single-host binding) -->
      <aside v-if="!auth.isKiosk" class="host-panel">
        <div class="hp-head">
          <span>Hosts</span>
          <button class="hp-refresh-btn" @click="loadHosts" title="Refresh"><svg class="u-d-block_h-14_w-14" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"/></svg></button>
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
            <!-- In SFTP mode the terminal is kept MOUNTED but hidden: the file
                 browser works over this session's SSH connection, and unmounting
                 the component closes the socket that holds it open. Hidden, not
                 destroyed. -->
            <TermSession
              v-if="activePane?.session"
              :class="{ 'pane-hidden': activePane.mode === 'sftp' }"
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
            <TermWatermark
              v-if="showWatermark && activePane?.session"
              :username="auth.user?.username || ''"
              :session-id="activePane.session.session_id"
            />
            <div v-else-if="!activePane?.session" class="pane-empty">
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
              :class="{ 'pane-hidden': splitPane.mode === 'sftp' }"
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
            <TermWatermark
              v-if="showWatermark && splitPane?.session"
              :username="auth.user?.username || ''"
              :session-id="splitPane.session.session_id"
            />
            <div v-else-if="!splitPane?.session" class="pane-empty">
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

      <!-- Right: SFTP file manager for the focused session. Keyed on session id so
           switching panes rebuilds it against the right connection rather than
           showing the previous host's listing. -->
      <TermFilePanel
        v-if="showFiles && focusedPane?.session"
        :key="focusedPane.session.session_id"
        :session-id="focusedPane.session.session_id"
        :host-label="focusedPane.name || focusedPane.label"
        :class="{ 'fm-full': focusedPane.mode === 'sftp' }"
        @close="onCloseFiles"
      />
    </div>

    <!-- ── Keyboard shortcuts ───────────────────────────────────────────────── -->
    <div v-if="showShortcuts" class="modal-overlay" style="z-index:530" @click.self="showShortcuts = false">
      <div class="sc-modal" @click.stop>
        <header class="sc-head">
          <span>Keyboard Shortcuts</span>
          <button class="fm-x" @click="showShortcuts = false" title="Close">✕</button>
        </header>
        <div class="sc-body">
          <section v-for="g in SHORTCUTS" :key="g.group">
            <h4>{{ g.group }}</h4>
            <div v-for="s in g.items" :key="s.keys" class="sc-row">
              <kbd>{{ s.keys }}</kbd><span>{{ s.what }}</span>
            </div>
          </section>
        </div>
      </div>
    </div>

    <!-- ── Context menu ─────────────────────────────────────────────────────── -->
    <div
      v-if="ctx.visible"
      class="ctx-menu"
      :style="{ top: ctx.y + 'px', left: ctx.x + 'px' }"
      @click.stop
    >
      <div class="ctx-header">{{ ctx.host?.name }}</div>
      <!-- Each login offers both entry points. SSH and SFTP are the same session
           underneath — SFTP just opens it straight into the file browser — so
           they belong on the credential, not as separate top-level verbs. -->
      <template v-if="ctx.credentials.length">
        <div class="ctx-section">Connect as</div>
        <div v-for="cred in ctx.credentials" :key="cred.id" class="ctx-cred">
          <span class="ctx-cred-name">
            {{ cred.username || cred.name }}
            <span v-if="cred.is_default" class="ctx-cred-tag">default</span>
          </span>
          <span class="ctx-cred-go">
            <button class="ctx-mini" @click="ctxConnectWithCred(cred, 'ssh')" title="Open a shell">▶ SSH</button>
            <button class="ctx-mini" @click="ctxConnectWithCred(cred, 'sftp')" title="Open the file browser">⁂ SFTP</button>
          </span>
        </div>
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
    <CredentialPicker
      :visible="credPicker.visible"
      :host="credPicker.host"
      :credentials="credPicker.credentials"
      :manual-allowed="credPicker.manualAllowed"
      :deep-link="credPicker.deepLink"
      :signed-in-as="auth.user?.username || ''"
      @close="closePicker"
      @pick="onCredPicked"
    />

    <!-- ── Zabbix deep-link confirmation — never auto-connect without an explicit click ── -->
  </div>
</template>

<script setup lang="ts">
import { ref, computed, reactive, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import TermSession from '@/components/terminal/TermSession.vue'
import TermFilePanel from '@/components/terminal/TermFilePanel.vue'
import TermWatermark from '@/components/terminal/TermWatermark.vue'
import CredentialPicker, { type ConnectMode } from '@/components/terminal/CredentialPicker.vue'
import api, { consumeInternalConnect } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useTerminalTheme } from '@/composables/useTerminalTheme'
import { useTerminalPanes, type Pane } from '@/composables/useTerminalPanes'
import { useTerminalSession } from '@/composables/useTerminalSession'

const auth = useAuthStore()

// ── Types ────────────────────────────────────────────────────────────────

interface Ctx { visible: boolean; x: number; y: number; host: any; credentials: any[] }

// ── State ─────────────────────────────────────────────────────────────────

const route = useRoute()

const fontSize = ref(14)
function adjustFontSize(delta: number) {
  fontSize.value = Math.min(28, Math.max(8, fontSize.value + delta))
}

// ── Terminal themes ─────────────────────────────────────────────────────────
// Palette data and the persisted choice live in composables/useTerminalTheme.ts.
// setTheme no longer closes the open menu — that was a caller concern, so the
// caller does it here and the composable stays free of menu state.
const { themeNames, themeName, currentTheme, setTheme: applyTheme } = useTerminalTheme()
function setTheme(name: string) { applyTheme(name); closeAll() }

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
const credPicker = reactive<{
  visible: boolean; host: any; targetPaneId: string; credentials: any[]; manualAllowed: boolean; deepLink: boolean
}>({ visible: false, host: null, targetPaneId: '', credentials: [], manualAllowed: false, deepLink: false })

const LOGIN_STORE_KEY = 'seyalrun.login.default'

/** The login this user last chose for a host, if they asked to remember it.
 *  Storage is unavailable inside Safari's third-party-iframe context (which is
 *  how this app runs embedded in Zabbix), so this degrades to "nothing saved"
 *  rather than throwing on every host click. */
function rememberedCredential(hostId: string): string {
  try {
    return (JSON.parse(localStorage.getItem(LOGIN_STORE_KEY) || '{}') || {})[hostId] || ''
  } catch {
    return ''
  }
}
// A link that opens this page and auto-connects (Zabbix deep-link) must never establish
// a live session with zero human awareness — even when there's exactly one authorized
// credential and the normal flow would otherwise skip straight past the picker.

// Panes and split — see composables/useTerminalPanes.ts
const {
  panes, activePaneId, splitId, splitDir, focusedSide,
  activePane, splitPane, focusedPane, activeSessionCount, paneAreaClass,
  addPane, closePane, findPane, openSplit, exitSplit,
} = useTerminalPanes({ isKiosk: () => auth.isKiosk })

// SFTP file manager visibility. Closed when the focused pane has no session —
// the panel is bound to a live connection, so keeping it open across a
// disconnect would show a listing that can no longer be acted on.
const showFiles = ref(false)
const showShortcuts = ref(false)
// Watermarking is on by default — it is a deterrent control, so making it
// opt-in would mean it is off exactly where nobody thought about it.
const showWatermark = ref(true)

// Documented rather than discovered. Every entry here is a binding that already
// exists — this lists them, it does not add any, so the map cannot drift from
// behaviour by being edited on its own.
const SHORTCUTS = [
  { group: 'Session', items: [
    { keys: 'Ctrl+C', what: 'Send SIGINT to the remote process' },
    { keys: 'Ctrl+D', what: 'Send EOF — usually ends the shell' },
    { keys: 'Ctrl+L', what: 'Clear the screen' },
  ]},
  { group: 'Terminal', items: [
    { keys: 'Ctrl/Cmd +', what: 'Increase font size' },
    { keys: 'Ctrl/Cmd -', what: 'Decrease font size' },
    { keys: 'Ctrl/Cmd S', what: 'Capture the screen as an image' },
    { keys: 'F11', what: 'Toggle fullscreen' },
  ]},
  { group: 'Files', items: [
    { keys: 'Double-click', what: 'Open a folder, or download a file' },
  ]},
  { group: 'Navigation', items: [
    { keys: 'Esc', what: 'Close any open menu or dialog' },
    { keys: 'Double-click tab', what: 'Rename the session' },
    { keys: 'Right-click host', what: 'Connect as a specific user' },
  ]},
]
/** Closing the browser in an SFTP-only pane has to give the terminal back —
 *  otherwise the pane is left showing nothing at all. */
async function onCloseFiles() {
  showFiles.value = false
  const pane = focusedPane.value
  if (!pane || pane.mode !== 'sftp') return

  // An SFTP pane ENDS when its file browser is closed. It previously fell back to
  // the terminal, on the reasoning that an empty pane is useless — but that hands
  // the operator an interactive shell they never asked for, on a host they chose
  // to reach for file transfer only. "I closed the file manager" is not a request
  // for a shell.
  //
  // The SSH session is also torn down rather than left running: it exists solely
  // to carry SFTP, and an idle session nobody can see is one that keeps a
  // credential unwrapped, holds a slot open and still appears in the live-session
  // list as though someone were working in it.
  const sessionId = pane.session?.session_id
  pane.session = null
  pane.mode = 'ssh'
  pane.disconnected = false
  pane.label = 'New'
  pane.hostId = null
  if (sessionId) {
    try {
      await api.delete(`/ssh/sessions/${sessionId}`)
    } catch {
      // Already gone (the socket may have closed first) — nothing to recover.
    }
  }
}

function toggleFiles() {
  if (!focusedPane.value?.session) return
  closeAll()
  showFiles.value = !showFiles.value
}

// Session lifecycle — see composables/useTerminalSession.ts
const { recentlyDisconnected, connectPane, onDisconnected, onReconnect } =
  useTerminalSession({ panes, hosts })

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

// ── Menu helpers ──────────────────────────────────────────────────────────

function toggleMenu(name: string) {
  activeMenu.value = activeMenu.value === name ? null : name
}

// Esc, on the window rather than on this element.
//
// The template had @keydown.esc.window — but `.window` is NOT a Vue modifier.
// Vue treats an unrecognised modifier on a key event as another KEY name, so
// that listener sat on this <div>, not on window. Keydown only reached it while
// focus was already inside the app; after clicking a menu item, focus is on
// <body>, the event never bubbles down into a child, and Esc did nothing. The
// shortcut map has been promising "Esc closes any open menu or dialog" the whole
// time — measured in a browser: the dialog stayed open.
function onWindowKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') closeAll()
}
onMounted(() => window.addEventListener('keydown', onWindowKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onWindowKeydown))

function closeAll() {
  activeMenu.value = null
  ctx.visible = false
  // The shortcut map documents "Esc — close any open menu or dialog", and Esc
  // routes here. It did not close this dialog, so the map described behaviour
  // that did not exist — caught by a browser check, not by the test that was
  // meant to prevent exactly that (it only spot-checked two of the bindings).
  showShortcuts.value = false
}

// ── Hosts ─────────────────────────────────────────────────────────────────

async function loadHosts() {
  hostsLoading.value = true
  hostsError.value = ''
  try {
    // Only hosts this user can actually open a session on. Listing all of
    // inventory meant clicking to find out — session create refuses correctly,
    // so nothing was exposed, but a list where most entries fail is not a list,
    // and it misrepresents the access model to whoever reads it.
    const resp = await api.get('/ssh/hosts')
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
  let manualAllowed = false
  try {
    const resp = await api.get('/ssh/credentials', { params: { host_id: host.id } })
    // The endpoint used to return a bare array and now returns an object; accept
    // both so a frontend deployed ahead of the service does not break the picker.
    creds = Array.isArray(resp.data) ? resp.data : (resp.data?.credentials ?? [])
    manualAllowed = Array.isArray(resp.data) ? false : !!resp.data?.manual_allowed
  } catch { /* fall through: connectPane surfaces the real error */ }

  // A remembered login connects straight away — that is the point of remembering
  // it. Only honoured when the credential is STILL authorized for this host, so a
  // revoked grant cannot be resurrected from a stale entry in someone's browser.
  const saved = rememberedCredential(host.id)
  if (saved && creds.some(c => c.id === saved)) {
    await connectPane(paneId, host, saved)
    return
  }

  if (creds.length === 1 && !manualAllowed) {
    await connectPane(paneId, host, creds[0].id)
    return
  }
  if (creds.length || manualAllowed) {
    credPicker.host = host
    credPicker.targetPaneId = paneId
    credPicker.credentials = creds
    credPicker.manualAllowed = manualAllowed
    credPicker.visible = true
    return
  }
  await connectPane(paneId, host)
}

/** SFTP opens the same SSH session and shows the file browser on top of it —
 *  file transfer rides that connection, so there is no second thing to connect. */
async function onCredPicked(p: { credentialId: string; mode: ConnectMode }) {
  const host = credPicker.host
  const paneId = credPicker.targetPaneId
  closePicker()
  if (!host) return
  const pane = findPane(paneId)
  if (pane) pane.mode = p.mode
  await connectPane(paneId, host, p.credentialId)
  if (p.mode === 'sftp') showFiles.value = true
}

// Zabbix deep-link entry point only — always requires an explicit click before a live
// session opens, regardless of how many authorized credentials are available.
/** Zabbix deep-link entry point.
 *
 *  Uses the SAME picker as an in-app click — there is no second connect UI to
 *  keep in step, which is how the dialog it replaces ended up rendering blank
 *  "Connect as" rows that nobody noticed. What it must NOT do is honour a
 *  remembered login and connect automatically: a link can be followed by someone
 *  who did not choose it, so a deep link always requires an explicit choice.
 *  That is the property the old dialog existed to provide, and it is kept.
 */
async function confirmZbxConnect(paneId: string, host: any) {
  let creds: any[] = []
  let manualAllowed = false
  try {
    const resp = await api.get('/ssh/credentials', { params: { host_id: host.id } })
    creds = Array.isArray(resp.data) ? resp.data : (resp.data?.credentials ?? [])
    manualAllowed = Array.isArray(resp.data) ? false : !!resp.data?.manual_allowed
  } catch { /* the picker shows its empty state */ }
  credPicker.host = host
  credPicker.targetPaneId = paneId
  credPicker.credentials = creds
  credPicker.manualAllowed = manualAllowed
  credPicker.deepLink = true
  credPicker.visible = true
}



function closePicker() {
  credPicker.manualAllowed = false
  credPicker.deepLink = false
  credPicker.visible = false
  credPicker.host = null
  credPicker.targetPaneId = ''
  credPicker.credentials = []
}

// ── Pane management ───────────────────────────────────────────────────────

function closeActivePane() {
  closeAll()
  closePane(activePaneId.value)
}

function newConnection() {
  if (auth.isKiosk) return   // defense in depth — the UI trigger is already hidden
  closeAll()
  addPane()
}

function doSplit(dir: 'h' | 'v') {
  closeAll()
  openSplit(dir)   // kiosk guard and the activePaneId restore both live in the composable
}

// ── SSH connect ───────────────────────────────────────────────────────────

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

function ctxConnectWithCred(cred: any, mode: ConnectMode = 'ssh') {
  const host = ctx.host
  closeAll()
  if (!host) return
  const paneId = targetPaneId()
  const pane = findPane(paneId)
  if (pane) pane.mode = mode
  connectPane(paneId, host, cred.id)
  if (mode === 'sftp') showFiles.value = true
}

function ctxSplitConnect(dir: 'h' | 'v') {
  const host = ctx.host
  closeAll()
  if (!host) return
  if (!splitId.value) {
    const p = openSplit(dir, host.name)
    if (p) connectPane(p.id, host)
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
    // An in-app connect (Hosts/Assets terminal icon) leaves a same-origin marker that a
    // Zabbix/external deep link cannot; only that path honours a remembered login and
    // auto-connects. Everything else — a Zabbix deep link, a pasted URL — takes the
    // deep-link path, which always requires an explicit login choice.
    const internal = !zbxHost && !ltToken && consumeInternalConnect(target.id)
    if (internal) {
      await connectWithCredPicker(activePaneId.value, target)
    } else {
      await confirmZbxConnect(activePaneId.value, target)
    }
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
.menu-capture.is-on { background: #1f6feb; border-color: #1f6feb; color: #fff; }
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
.sc-modal {
  background: #0d1117; border: 1px solid #30363d; border-radius: 8px;
  width: 460px; max-width: 92vw; max-height: 80vh; overflow: hidden;
  display: flex; flex-direction: column; color: #c9d1d9;
}
.sc-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px; border-bottom: 1px solid #21262d; font-weight: 600; color: #e6edf3;
}
.sc-body { padding: 6px 14px 14px; overflow-y: auto; }
.sc-body h4 { margin: 14px 0 6px; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: #8b949e; }
.sc-row { display: flex; align-items: center; gap: 10px; padding: 3px 0; font-size: 13px; }
.sc-row kbd {
  flex: 0 0 130px; background: #21262d; border: 1px solid #30363d; border-radius: 4px;
  padding: 2px 6px; font: 500 11px/1.5 ui-monospace, monospace; text-align: center;
}
.ctx-cred {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  padding: 4px 10px;
}
.ctx-cred:hover { background: #161b22; }
.ctx-cred-name { display: flex; align-items: center; gap: 5px; min-width: 0; font-size: 12.5px; }
.ctx-cred-tag {
  font-size: 9px; text-transform: uppercase; letter-spacing: .04em;
  padding: 1px 5px; border-radius: 7px; background: #1f6feb; color: #fff;
}
.ctx-cred-go { display: flex; gap: 4px; flex: 0 0 auto; }
.ctx-mini {
  background: #21262d; border: 1px solid #30363d; color: #c9d1d9;
  border-radius: 4px; padding: 2px 7px; cursor: pointer; font-size: 11px;
}
.ctx-mini:hover { background: #30363d; color: #e6edf3; }
/* SFTP-only: the terminal keeps running (the file browser needs its connection)
   but takes no space, and the browser expands to the full pane. */
.pane-hidden { display: none !important; }
.fm-full { width: 100% !important; min-width: 0 !important; border-left: 0; }
</style>
