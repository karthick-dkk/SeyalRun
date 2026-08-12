<template>
  <div class="term-wrapper">

    <!-- Shown while WebSocket handshake is in progress -->
    <div v-if="wsState === 'connecting'" class="status-overlay">
      <span class="spin-icon">◌</span>
      <span>Connecting…</span>
    </div>

    <!-- Reconnect overlay — shown when connection is lost -->
    <div v-if="wsState === 'closed'" class="reconnect-overlay">
      <div class="reconnect-box">
        <svg class="reconnect-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5.636 5.636a9 9 0 1012.728 0M12 3v9"/></svg>
        <p class="reconnect-title">{{ sessionError ? 'Connection Failed' : 'Session Disconnected' }}</p>

        <!-- Structured, copyable error details -->
        <div v-if="sessionError && errInfo" class="err-detail">
          <button class="err-copy" @click="copyError" :title="copied ? 'Copied!' : 'Copy error'">{{ copied ? '✓ Copied' : '⧉ Copy' }}</button>
          <div class="err-row"><span class="err-k">User</span><span class="err-v">{{ errInfo.user || '—' }}</span></div>
          <div class="err-row"><span class="err-k">Host</span><span class="err-v">{{ errInfo.host }}<template v-if="errInfo.address && errInfo.address !== errInfo.host"> ({{ errInfo.address }})</template></span></div>
          <div class="err-row"><span class="err-k">SSH Port</span><span class="err-v">{{ errInfo.port || 22 }}</span></div>
          <div class="err-row err-row--msg"><span class="err-k">Error</span><span class="err-v err-v--msg">{{ errInfo.detail || disconnectReason }}</span></div>
        </div>
        <p v-else class="reconnect-sub" :class="{ 'reconnect-err': sessionError }">{{ disconnectReason || 'The SSH session ended.' }}</p>

        <button class="reconnect-btn" @click="emit('reconnect')">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:14px;height:14px"><path d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"/></svg>
          Reconnect
        </button>
      </div>
    </div>

    <!-- xterm.js mounts here -->
    <div class="term-container" ref="containerRef" @contextmenu.prevent="onContextMenu" @mouseup="onMouseUp" />

    <!-- Right-click terminal options menu -->
    <div v-if="ctxMenu.visible" class="term-ctx-backdrop" @click="ctxMenu.visible = false" @contextmenu.prevent="ctxMenu.visible = false" />
    <div v-if="ctxMenu.visible" class="term-ctx-menu" :style="{ left: ctxMenu.x + 'px', top: ctxMenu.y + 'px' }">
      <button class="term-ctx-item" :disabled="!hasSelection" @click="ctxAction(() => { const s = term?.getSelection(); if (s) navigator.clipboard.writeText(s).catch(() => {}) })">⧉ Copy</button>
      <button class="term-ctx-item" @click="ctxAction(pasteFromClipboard)">📋 Paste</button>
      <button class="term-ctx-item" @click="ctxAction(snip)">📷 Capture Screen</button>
      <div class="term-ctx-sep" />
      <div class="term-ctx-row">
        <span>Font Size</span>
        <span class="term-ctx-stepper">
          <button @click="ctxAction(() => emit('font-size-delta', -1))">−</button>
          <button @click="ctxAction(() => emit('font-size-delta', 1))">+</button>
        </span>
      </div>
      <div class="term-ctx-sep" />
      <button class="term-ctx-item" @click="ctxAction(() => emit('split', 'h'))">⊞ Split Horizontal</button>
      <button class="term-ctx-item" @click="ctxAction(() => emit('split', 'v'))">⊟ Split Vertical</button>
    </div>

    <!-- Command-confirm overlay -->
    <div v-if="pendingConfirm" class="confirm-overlay">
      <div class="confirm-box">
        <p class="confirm-title">Command requires approval</p>
        <code class="confirm-cmd">{{ pendingConfirm.command }}</code>
        <p class="confirm-filter">Policy: {{ pendingConfirm.filter }}</p>
        <div class="confirm-actions">
          <button class="btn btn-danger"  @click="sendConfirm(false)">Block</button>
          <button class="btn btn-primary" @click="sendConfirm(true)">Allow</button>
        </div>
      </div>
    </div>

  </div>
</template>

<script setup lang="ts">
import { reactive, ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { wsUrl } from '@/api/client'
import { useCapturesStore } from '@/stores/captures'
import '@xterm/xterm/css/xterm.css'

const captures = useCapturesStore()

const props = defineProps<{
  sessionId: string
  wsPath:    string
  fontSize?: number
  theme?:    Record<string, string>
}>()

const DEFAULT_THEME: Record<string, string> = {
  background:          '#1a1b1e',
  foreground:          '#dce1e7',
  cursor:              '#dce1e7',
  cursorAccent:        '#1a1b1e',
  selectionBackground: '#3a4a5a',
  black:   '#1c1e26', red:     '#ff5c57', green:   '#5af78e', yellow:  '#f3f99d',
  blue:    '#57c7ff', magenta: '#ff6ac1', cyan:    '#9aedfe', white:   '#f1f1f0',
  brightBlack:   '#686868', brightRed:   '#ff6e6e', brightGreen:   '#69ff94',
  brightYellow:  '#ffffa5', brightBlue:  '#d6acff', brightMagenta: '#ff92df',
  brightCyan:    '#a4ffff', brightWhite: '#ffffff',
}

const emit = defineEmits<{
  (e: 'disconnected'): void
  (e: 'reconnect'): void
  (e: 'split', dir: 'h' | 'v'): void
  (e: 'font-size-delta', delta: number): void
}>()

const containerRef = ref<HTMLElement | null>(null)
const wsState = ref<'connecting' | 'open' | 'closed'>('connecting')

let term:       Terminal | null = null
let fitAddon:   FitAddon | null = null
let ws:         WebSocket | null = null
let pingTimer:  ReturnType<typeof setInterval>  | null = null
let fitTimer:   ReturnType<typeof setTimeout>   | null = null
let winResizeFn: (() => void) | null = null

// Track last PTY size sent — skip resize if unchanged (avoids redundant SIGWINCH)
let _sentCols = 0
let _sentRows = 0


const pendingConfirm   = ref<{ command: string; filter: string; filter_id: string } | null>(null)
const disconnectReason = ref('')
const sessionError = ref(false)
const errInfo = ref<{ user: string; host: string; address: string; port: string | number; detail: string } | null>(null)
const copied = ref(false)

async function copyError() {
  const e = errInfo.value
  const text = e
    ? `User:     ${e.user || '—'}\nHost:     ${e.host}${e.address && e.address !== e.host ? ` (${e.address})` : ''}\nSSH Port: ${e.port || 22}\nError:    ${e.detail || disconnectReason.value}`
    : disconnectReason.value
  try {
    await navigator.clipboard.writeText(text)
    copied.value = true
    setTimeout(() => { copied.value = false }, 1500)
  } catch { /* clipboard unavailable */ }
}

function sendConfirm(allow: boolean) {
  if (ws?.readyState === WebSocket.OPEN)
    ws.send(JSON.stringify({ type: 'confirm', allow }))
  pendingConfirm.value = null
}

// ── Paste helpers ───────────────────────────────────────────────────────────
function pasteFromClipboard() {
  navigator.clipboard.readText().catch(() => '').then(text => {
    if (text && ws?.readyState === WebSocket.OPEN)
      ws.send(JSON.stringify({ type: 'input', data: text }))
  })
}

// ── Right-click context menu ─────────────────────────────────────────────────
const ctxMenu = reactive({ visible: false, x: 0, y: 0 })
const hasSelection = ref(false)
function onContextMenu(e: MouseEvent) {
  hasSelection.value = !!term?.hasSelection()
  ctxMenu.x = e.clientX
  ctxMenu.y = e.clientY
  ctxMenu.visible = true
}
function ctxAction(fn: () => void) {
  ctxMenu.visible = false
  fn()
}
// Click-drag-select auto-copies (PuTTY/iTerm2 convention) — checked on mouseup
// rather than xterm's onSelectionChange, which fires continuously mid-drag and
// would otherwise hit the clipboard dozens of times for one selection.
function onMouseUp() {
  if (term?.hasSelection()) {
    const s = term.getSelection()
    if (s) navigator.clipboard.writeText(s).catch(() => {})
  }
}

// ── Safe fit: only resize terminal if dimensions would actually change ──────
// This prevents the ResizeObserver feedback loop that sends multiple identical
// or oscillating resize messages → multiple SIGWINCH → multiple prompt redraws
function safeFit() {
  if (!fitAddon || !term) return
  const proposed = fitAddon.proposeDimensions()
  if (!proposed || isNaN(proposed.cols) || isNaN(proposed.rows)) return
  if (proposed.cols === term.cols && proposed.rows === term.rows) return
  fitAddon.fit()
}

onMounted(async () => {
  if (!containerRef.value) return

  term = new Terminal({
    theme: props.theme ?? DEFAULT_THEME,
    fontFamily:   'Menlo, Monaco, "SF Mono", "Fira Code", "JetBrains Mono", "Cascadia Code", monospace',
    fontSize:     props.fontSize ?? 14,
    lineHeight:   1,         // MUST stay 1.0 — higher values shift cursor below text glyph
    letterSpacing: 0,
    cursorBlink:  true,
    cursorStyle:  'block',
    scrollback:   5000,
    convertEol:   false,
    allowTransparency: false,
    macOptionIsMeta:       true,
    altClickMovesCursor:   true,
    rightClickSelectsWord: false,
  })

  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.open(containerRef.value)

  // Double-RAF: let the browser settle the flex layout for two full frames
  // so fitAddon.fit() measures the FINAL container size. This ensures the
  // PTY is created with the exact correct cols×rows from the first frame —
  // zero SIGWINCH needed, cursor stays on the prompt line.
  await nextTick()
  await new Promise<void>(r =>
    requestAnimationFrame(() => requestAnimationFrame(() => { fitAddon!.fit(); r() }))
  )

  term.focus()
  _sentCols = term.cols
  _sentRows = term.rows

  // ── Window-resize handler (replaces ResizeObserver) ─────────────────────
  // ResizeObserver on the xterm container creates a feedback loop:
  // fit() → canvas changes → ResizeObserver fires → fit() → different size →
  // resize sent → SIGWINCH → zsh redraws prompt on next line.
  // window.resize fires only on actual browser window changes — no loop.
  winResizeFn = () => {
    if (fitTimer) clearTimeout(fitTimer)
    fitTimer = setTimeout(safeFit, 100)
  }
  window.addEventListener('resize', winResizeFn)

  // ── Open WebSocket ──────────────────────────────────────────────────────
  const cleanPath = props.wsPath.replace(/^\/ws\//, '')
  const url = `${wsUrl(cleanPath)}&cols=${_sentCols}&rows=${_sentRows}`
  ws = new WebSocket(url)

  ws.onopen = () => {
    wsState.value = 'open'
    term?.focus()
  }

  ws.onmessage = ({ data }) => {
    try {
      const msg = JSON.parse(data)
      if (msg.type === 'output') {
        term?.write(msg.data)
      } else if (msg.type === 'confirm_required') {
        pendingConfirm.value = { command: msg.command, filter: msg.filter, filter_id: msg.filter_id }
      } else if (msg.type === 'error') {
        // Surface the failure in the disconnect dialog (not the terminal background).
        sessionError.value = true
        disconnectReason.value = String(msg.detail || msg.message || 'SSH connection failed')
        errInfo.value = {
          user: msg.user || '',
          host: msg.host || '',
          address: msg.address || '',
          port: msg.port || '',
          detail: String(msg.detail || msg.message || ''),
        }
      }
    } catch {
      term?.write(data)
    }
  }

  ws.onclose = ({ code, reason }) => {
    wsState.value = 'closed'
    if (sessionError.value) {
      // An SSH/auth error was already reported — keep that message, don't overwrite
      // it with "ended normally".
    } else if (code === 1000 || code === 1001) {
      disconnectReason.value = 'Session ended normally.'
      term?.writeln('\r\n\x1b[2m──────────────── Session closed ────────────────\x1b[0m')
    } else {
      const why = reason || `code ${code}`
      sessionError.value = true
      disconnectReason.value = `Connection lost (${why}).`
      term?.writeln(`\r\n\x1b[33m──────────────── Disconnected (${why}) ────────────────\x1b[0m`)
    }
    emit('disconnected')
  }

  ws.onerror = () => {
    term?.writeln('\r\n\x1b[31m  WebSocket error — check network / server logs.\x1b[0m')
  }

  // ── Input ───────────────────────────────────────────────────────────────
  // Ctrl+D → \x04 → EOF to shell; handled natively by xterm + forwarded here
  term.onData(data => {
    if (ws?.readyState === WebSocket.OPEN)
      ws.send(JSON.stringify({ type: 'input', data }))
  })

  // ── Keyboard shortcuts ──────────────────────────────────────────────────
  term.attachCustomKeyEventHandler(evt => {
    if (evt.type !== 'keydown') return true
    if (evt.ctrlKey && evt.shiftKey) {
      if (evt.key === 'C') {
        const sel = term?.getSelection()
        if (sel) navigator.clipboard.writeText(sel).catch(() => {})
        return false
      }
      if (evt.key === 'V' || evt.key === 'P') {
        pasteFromClipboard()
        return false
      }
    }
    // Plain Ctrl+V (Windows/Linux) / Cmd+V (Mac) — paste, alongside the
    // Ctrl+Shift+V above. Terminals conventionally reserve bare Ctrl+V for a
    // literal control character, but a browser-embedded terminal's users
    // overwhelmingly expect their OS's normal paste shortcut to just work.
    if ((evt.ctrlKey && !evt.shiftKey && evt.key.toLowerCase() === 'v') ||
        (evt.metaKey && evt.key.toLowerCase() === 'v')) {
      pasteFromClipboard()
      return false
    }
    // Ctrl+S / Cmd+S — capture screen (must preventDefault or the browser's own
    // "Save Page" dialog opens instead).
    if ((evt.ctrlKey || evt.metaKey) && evt.key.toLowerCase() === 's') {
      evt.preventDefault()
      snip()
      return false
    }
    return true
  })

  // ── Resize: send only when dimensions actually change ───────────────────
  // The server (terminal.py) also guards with pty_cols/pty_rows — double
  // protection so SIGWINCH only fires on genuine terminal size changes.
  term.onResize(({ cols, rows }) => {
    if (cols === _sentCols && rows === _sentRows) return
    _sentCols = cols
    _sentRows = rows
    if (ws?.readyState === WebSocket.OPEN)
      ws.send(JSON.stringify({ type: 'resize', cols, rows }))
  })

  // ── Keep-alive ping ─────────────────────────────────────────────────────
  pingTimer = setInterval(() => {
    if (ws?.readyState === WebSocket.OPEN)
      ws.send(JSON.stringify({ type: 'ping' }))
  }, 30_000)
})

onBeforeUnmount(() => {
  if (pingTimer)  clearInterval(pingTimer)
  if (fitTimer)   clearTimeout(fitTimer)
  if (winResizeFn) window.removeEventListener('resize', winResizeFn)
  ws?.close()
  term?.dispose()
})

watch(() => props.theme, t => {
  if (term && t) term.options.theme = t
})

watch(() => props.fontSize, size => {
  if (term && size) {
    term.options.fontSize = size
    if (fitTimer) clearTimeout(fitTimer)
    fitTimer = setTimeout(safeFit, 100)
  }
})

function snip() {
  if (!containerRef.value || !term) return
  const canvases = Array.from(containerRef.value.querySelectorAll('canvas')) as HTMLCanvasElement[]
  if (!canvases.length) return
  // Composite all xterm canvas layers (text, cursor, selection) onto one with the terminal background.
  const w = canvases[0].width
  const h = canvases[0].height
  const composite = document.createElement('canvas')
  composite.width = w
  composite.height = h
  const ctx = composite.getContext('2d')!
  ctx.fillStyle = '#1a1b1e'
  ctx.fillRect(0, 0, w, h)
  for (const c of canvases) ctx.drawImage(c, 0, 0)

  // canvas.toBlob is always async, but navigator.clipboard.write() requires a
  // user-activation context — by the time toBlob's callback fires, that
  // activation window has already closed in most browsers, so a plain
  // `.then(blob => clipboard.write(...))` silently fails (caught and
  // swallowed, which is why capture-then-paste showed nothing). The fix: the
  // Clipboard API spec explicitly allows a ClipboardItem's value to be a
  // Promise<Blob> for exactly this case — constructing the ClipboardItem (and
  // calling write()) SYNCHRONOUSLY, right here in the click handler, keeps us
  // inside the activation window even though the blob itself resolves later.
  const blobPromise = new Promise<Blob>((resolve, reject) => {
    composite.toBlob((b) => (b ? resolve(b) : reject(new Error('toBlob failed'))), 'image/png')
  })

  if (navigator.clipboard?.write && typeof ClipboardItem !== 'undefined') {
    navigator.clipboard.write([new ClipboardItem({ 'image/png': blobPromise })]).catch(() => {})
  }

  blobPromise.then((blob) => {
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `terminal-${props.sessionId.slice(0, 8)}-${Date.now()}.png`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)

    // Also drop it in the captures tray (topbar bell) — expires after an hour,
    // and can be re-copied from there even if the first clipboard write above
    // didn't land (e.g. an older browser without the Promise<Blob> support).
    const reader = new FileReader()
    reader.onload = () => {
      if (typeof reader.result === 'string') captures.add(reader.result, props.sessionId.slice(0, 8))
    }
    reader.readAsDataURL(blob)
  }).catch(() => {})
}

defineExpose({
  focus:         () => term?.focus(),
  resize:        () => safeFit(),
  copySelection: () => { const s = term?.getSelection(); if (s) navigator.clipboard.writeText(s).catch(() => {}) },
  pasteText:     () => pasteFromClipboard(),
  clear:         () => { term?.clear(); term?.focus() },
  snip,
})
</script>

<style scoped>
.term-wrapper {
  display: flex;
  flex: 1;
  min-height: 0;
  position: relative;
  overflow: hidden;
  background: #1a1b1e;
}

/* The xterm container must be flex:1 AND align-self:stretch so
   fitAddon.fit() always measures the correct dimensions. */
.term-container {
  flex: 1;
  min-height: 0;
  align-self: stretch;
  overflow: hidden;
  cursor: text;
}

:deep(.xterm)        { width: 100%; height: 100%; }
:deep(.xterm-screen) { display: block; }

:deep(.xterm-viewport)                  { overflow-y: scroll; }
:deep(.xterm-viewport)::-webkit-scrollbar       { width: 6px; }
:deep(.xterm-viewport)::-webkit-scrollbar-track { background: transparent; }
:deep(.xterm-viewport)::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.15); border-radius: 3px; }
:deep(.xterm-viewport)::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.28); }

/* Connecting overlay */
.status-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  background: #1a1b1e;
  color: #8b949e;
  font-family: Menlo, Monaco, "SF Mono", monospace;
  font-size: 13px;
  z-index: 5;
  pointer-events: none;
}
.spin-icon {
  display: inline-block;
  animation: spin 1s linear infinite;
  font-size: 16px;
  color: #57c7ff;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* Reconnect overlay */
.reconnect-overlay {
  position: absolute; inset: 0;
  background: rgba(26, 27, 30, 0.88);
  display: flex; align-items: center; justify-content: center;
  z-index: 8;
}
.reconnect-box {
  display: flex; flex-direction: column; align-items: center; gap: 10px;
  background: #1f2430; border: 1px solid #3a4a5a; border-radius: 12px;
  padding: 28px 36px; max-width: 560px; width: 90%;
  box-shadow: 0 20px 60px rgba(0,0,0,0.7); text-align: center;
}
.reconnect-icon {
  width: 40px; height: 40px; color: #57c7ff; margin-bottom: 4px;
}
.reconnect-title { font-size: 16px; font-weight: 700; color: #f1f1f0; margin: 0; }
.reconnect-sub   { font-size: 13px; color: #b9bbbe; margin: 0; max-width: 440px; word-break: break-word; line-height: 1.5; }
.reconnect-err   { color: #ff6b6b; font-weight: 600; }

/* Structured error details in the disconnect dialog */
.err-detail {
  position: relative; text-align: left; width: 100%; max-width: 520px;
  background: #0d1117; border: 1px solid rgba(248,81,73,0.4); border-radius: 8px;
  padding: 12px 14px; display: flex; flex-direction: column; gap: 6px; margin: 4px 0 2px;
}
.err-row { display: grid; grid-template-columns: 80px 1fr; gap: 10px; font-size: 12.5px; align-items: start; }
.err-k { color: #8b949e; font-weight: 600; }
.err-v { color: #e6edf3; font-family: var(--font-mono, monospace); word-break: break-word; }
.err-v--msg { color: #ff7b72; }
.err-row--msg { border-top: 1px solid #21262d; padding-top: 6px; margin-top: 2px; }
.err-copy {
  position: absolute; top: 8px; right: 8px;
  background: #21262d; border: 1px solid #30363d; color: #c9d1d9;
  font-size: 11px; padding: 3px 8px; border-radius: 5px; cursor: pointer;
}
.err-copy:hover { border-color: #58a6ff; color: #fff; }
.reconnect-btn {
  display: flex; align-items: center; gap: 7px;
  margin-top: 8px; padding: 9px 22px;
  background: #57c7ff; color: #1a1b1e;
  border: none; border-radius: 7px;
  font-size: 13px; font-weight: 700;
  cursor: pointer; transition: opacity 0.15s;
}
.reconnect-btn:hover { opacity: 0.85; }

/* Confirm overlay */
.confirm-overlay {
  position: absolute; inset: 0;
  background: rgba(0,0,0,0.72);
  display: flex; align-items: center; justify-content: center;
  z-index: 10;
}
.confirm-box {
  background: #1f2430; border: 1px solid #3a4a5a; border-radius: 10px;
  padding: 24px; max-width: 480px; width: 90%;
  box-shadow: 0 16px 48px rgba(0,0,0,0.7);
}
.confirm-title  { font-weight: 700; font-size: 15px; margin: 0 0 12px; color: #f3f99d; }
.confirm-cmd    {
  display: block; background: #1a1b1e; padding: 10px 14px; border-radius: 5px;
  font-family: Menlo, Monaco, monospace; font-size: 13px; margin-bottom: 8px;
  word-break: break-all; color: #9aedfe;
}
.confirm-filter { font-size: 12px; color: #686868; margin-bottom: 16px; }
.confirm-actions { display: flex; gap: 10px; justify-content: flex-end; }
.btn         { padding: 6px 18px; border-radius: 6px; border: none; font-size: 13px; font-weight: 600; cursor: pointer; }
.btn-danger  { background: #ff5c57; color: #fff; }
.btn-primary { background: #57c7ff; color: #1a1b1e; }
.btn:hover   { opacity: 0.85; }

/* Right-click terminal options menu — this whole component stays permanently
   dark (matches the live-terminal convention), so these use fixed colors like
   everything else here rather than the site's light/dark theme variables. */
.term-ctx-backdrop { position: fixed; inset: 0; z-index: 400; }
.term-ctx-menu {
  position: fixed; z-index: 401; min-width: 190px;
  background: #21262d; border: 1px solid #3a4a5a; border-radius: 8px;
  padding: 4px; box-shadow: 0 12px 32px rgba(0,0,0,0.6);
  font-size: 13px; color: #dce1e7;
}
.term-ctx-item {
  display: block; width: 100%; text-align: left; padding: 7px 10px;
  background: none; border: none; border-radius: 5px; color: inherit;
  font-size: 13px; cursor: pointer;
}
.term-ctx-item:hover:not(:disabled) { background: #30363d; }
.term-ctx-item:disabled { opacity: 0.4; cursor: not-allowed; }
.term-ctx-sep { height: 1px; background: #3a4a5a; margin: 4px 2px; }
.term-ctx-row { display: flex; align-items: center; justify-content: space-between; padding: 6px 10px; }
.term-ctx-stepper { display: flex; gap: 4px; }
.term-ctx-stepper button {
  width: 22px; height: 22px; border-radius: 4px; border: 1px solid #3a4a5a;
  background: #1a1b1e; color: #dce1e7; cursor: pointer; font-size: 14px; line-height: 1;
}
.term-ctx-stepper button:hover { background: #30363d; }
</style>
