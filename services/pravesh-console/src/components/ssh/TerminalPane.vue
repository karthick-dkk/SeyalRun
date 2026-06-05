<template>
  <div
    ref="wrapEl"
    class="term-pane"
    :class="{ 'pane-active': active, 'pane-error': state === 'error', 'pane-closed': state === 'closed' }"
    @click="$emit('activate')"
    @contextmenu.prevent="onRightClick"
  >
    <!-- Pane title bar -->
    <div class="pane-bar">
      <span class="pane-dot" :class="`dot-${state}`" />
<span class="pane-label">{{ label }}</span>
      <span v-if="state === 'connecting'" class="pane-connecting">connecting…</span>
      <div class="pane-actions">
        <span class="pane-size mono">{{ cols }}×{{ rows }}</span>
        <!-- Locked: primary pane while sub-panes exist -->
        <button
          v-if="!closable"
          class="pane-close pane-close-locked"
          title="Close sub-sessions first (or use the tab ✕ to close all)"
          @click.stop="$emit('close-blocked')"
        >🔒</button>
        <!-- Normal close -->
        <button
          v-else
          class="pane-close"
          :title="isPrimary ? 'Close this session' : 'Close pane'"
          @click.stop="$emit('close')"
        >✕</button>
      </div>
    </div>

    <!-- Terminal container -->
    <div ref="termEl" class="term-body" />

    <!-- Reconnect overlay (when closed/error) -->
    <div v-if="state === 'closed' || state === 'error'" class="pane-overlay">
      <div class="overlay-msg">
        <div class="overlay-icon">{{ state === 'error' ? '✗' : '○' }}</div>
        <div>{{ state === 'error' ? 'Connection failed' : 'Session ended' }}</div>
        <button class="overlay-btn" @click.stop="$emit('reconnect')">↺ Reconnect</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'

const props = defineProps<{
  sessionId: string
  token: string
  label: string
  active: boolean
  closable?: boolean   // false = locked (primary with sub-panes)
  isPrimary?: boolean  // true = first pane in tab
}>()

const emit = defineEmits<{
  activate: []
  close: []
  'close-blocked': []  // fired when locked close is clicked
  reconnect: []
  'state-change': [state: string]
  'right-click': [x: number, y: number]
}>()

const wrapEl = ref<HTMLElement>()
const termEl = ref<HTMLElement>()
const state = ref<'connecting' | 'connected' | 'closed' | 'error'>('connecting')
const cols = ref(80)
const rows = ref(24)

let term: Terminal | null = null
let fit: FitAddon | null = null
let ws: WebSocket | null = null
let ro: ResizeObserver | null = null
let pingTimer: ReturnType<typeof setInterval> | null = null

function onRightClick(e: MouseEvent) {
  emit('right-click', e.clientX, e.clientY)
}

function focus() { term?.focus() }

function write(text: string) { term?.write(text) }

watch(() => props.active, (isActive) => {
  if (isActive) {
    setTimeout(() => {
      fit?.fit()
      term?.focus()
    }, 50)
  }
})

function initTerm() {
  term = new Terminal({
    theme: {
      background: '#010409', foreground: '#e6edf3', cursor: '#58a6ff',
      cursorAccent: '#010409',
      selectionBackground: 'rgba(88,166,255,0.3)',
      black: '#0d1117', red: '#f85149', green: '#3fb950', yellow: '#d29922',
      blue: '#58a6ff', magenta: '#bc8cff', cyan: '#39c5cf', white: '#8b949e',
      brightBlack: '#21262d', brightRed: '#f85149', brightGreen: '#56d364',
      brightYellow: '#e3b341', brightBlue: '#79c0ff', brightMagenta: '#d2a8ff',
      brightCyan: '#39c5cf', brightWhite: '#e6edf3',
    },
    fontFamily: '"JetBrains Mono", "Cascadia Code", "Fira Code", ui-monospace, monospace',
    fontSize: 14,
    lineHeight: 1.35,
    letterSpacing: 0,
    cursorBlink: true,
    cursorStyle: 'block',
    scrollback: 10000,
    allowProposedApi: true,
    convertEol: false,
    windowsMode: false,
  })

  fit = new FitAddon()
  term.loadAddon(fit)
  term.loadAddon(new WebLinksAddon())
  term.open(termEl.value!)
  fit.fit()
  cols.value = term.cols
  rows.value = term.rows

  if (props.active) term.focus()

  // Forward typed input to WebSocket immediately
  term.onData((data) => {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'input', data }))
    }
  })

  // Resize observer on the wrapper
  ro = new ResizeObserver(() => {
    requestAnimationFrame(() => {
      fit?.fit()
      if (!term) return
      cols.value = term.cols
      rows.value = term.rows
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
      }
    })
  })
  ro.observe(wrapEl.value!)
}

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${location.host}/ws/ssh/${props.sessionId}?token=${props.token}`)

  ws.onopen = () => {
    pingTimer = setInterval(() => {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }))
      }
    }, 25000)
  }

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data)
      switch (msg.type) {
        case 'connected':
          state.value = 'connected'
          term?.write(msg.data ?? '')
          term?.focus()
          emit('state-change', 'connected')
          break
        case 'output':
          term?.write(msg.data ?? '')
          break
        case 'closed':
          state.value = 'closed'
          term?.writeln('\r\n\x1b[33m[Session closed]\x1b[0m')
          emit('state-change', 'closed')
          break
        case 'error':
          state.value = 'error'
          term?.writeln(`\r\n\x1b[31m[Error: ${msg.data}]\x1b[0m`)
          emit('state-change', 'error')
          break
      }
    } catch { /* ignore */ }
  }

  ws.onerror = () => {
    state.value = 'error'
    term?.writeln('\r\n\x1b[31m[WebSocket error]\x1b[0m')
    emit('state-change', 'error')
  }

  ws.onclose = () => {
    if (pingTimer) clearInterval(pingTimer)
    if (state.value === 'connected') {
      state.value = 'closed'
      emit('state-change', 'closed')
    }
  }
}

defineExpose({ focus, write, cols, rows, state })

onMounted(() => {
  initTerm()
  term?.writeln(`\x1b[36mConnecting to session…\x1b[0m`)
  connect()
})

onUnmounted(() => {
  if (pingTimer) clearInterval(pingTimer)
  ws?.close()
  term?.dispose()
  ro?.disconnect()
})
</script>

<style scoped>
.term-pane {
  display: flex;
  flex-direction: column;
  background: #010409;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  overflow: hidden;
  position: relative;
  min-height: 0;
  min-width: 0;
}
.pane-active { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
.pane-error { border-color: var(--error); }
.pane-closed { border-color: var(--border-muted); }

.pane-bar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 3px var(--space-3);
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  user-select: none;
}
.pane-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.dot-connecting { background: var(--warning); animation: pulse 1s infinite; }
.dot-connected { background: var(--success); box-shadow: 0 0 5px var(--success); }
.dot-closed { background: var(--text-subtle); }
.dot-error { background: var(--error); }
@keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:.3 } }

.pane-label { font-size: 11px; font-weight: 600; color: var(--text); font-family: var(--font-mono); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pane-connecting { font-size: 10px; color: var(--warning); }
.pane-actions { display: flex; align-items: center; gap: var(--space-2); flex-shrink: 0; }
.pane-size { font-size: 10px; color: var(--text-subtle); }
.pane-badge-main {
  font-size: 9px;
  font-weight: 700;
  color: var(--accent);
  background: var(--accent-dim);
  padding: 1px 5px;
  border-radius: var(--radius-full);
  flex-shrink: 0;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.pane-close {
  background: none; border: none; color: var(--text-subtle);
  cursor: pointer; font-size: 11px; padding: 2px 5px; border-radius: 3px;
  opacity: 1; transition: all var(--transition);
}
.pane-close:hover { color: var(--error); background: var(--error-dim); }

.pane-close-locked {
  cursor: not-allowed;
  opacity: 0.7;
  font-size: 10px;
}
.pane-close-locked:hover { color: var(--warning); background: var(--warning-dim); }

.term-body { flex: 1; overflow: hidden; padding: 4px; cursor: text; min-height: 0; }
:deep(.xterm) { height: 100%; }
:deep(.xterm-viewport) { background: transparent !important; }
:deep(.xterm-screen) { outline: none !important; }

.pane-overlay {
  position: absolute; inset: 0; background: rgba(1,4,9,0.85);
  display: flex; align-items: center; justify-content: center;
  backdrop-filter: blur(2px);
}
.overlay-msg { text-align: center; display: flex; flex-direction: column; align-items: center; gap: var(--space-3); }
.overlay-icon { font-size: 28px; color: var(--text-muted); }
.overlay-msg div { font-size: 13px; color: var(--text-muted); }
.overlay-btn { background: var(--bg-overlay); border: 1px solid var(--border-muted); color: var(--text-muted); border-radius: var(--radius-md); padding: var(--space-2) var(--space-4); font-size: 12px; cursor: pointer; transition: all 150ms; }
.overlay-btn:hover { color: var(--accent); border-color: var(--accent); }
</style>
