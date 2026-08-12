<template>
  <AppShell>
    <div class="sp-wrap">

      <!-- Header -->
      <div class="sp-header">
        <router-link to="/sessions" class="sp-back">&#8592; Sessions</router-link>
        <div v-if="meta" class="sp-meta">
          <span class="sp-meta-host">{{ meta.session_info?.host_name || '—' }}</span>
          <span class="sp-meta-sep">·</span>
          <span class="sp-meta-user">{{ meta.session_info?.username || '—' }}</span>
          <span class="sp-meta-sep">·</span>
          <span class="sp-meta-dur">{{ fmtDuration(meta.duration_seconds) }}</span>
          <span class="sp-meta-sep">·</span>
          <span class="sp-meta-date">{{ fmtDate(meta.created_at) }}</span>
        </div>
      </div>

      <div v-if="loading" class="sp-loading">Loading recording…</div>
      <div v-else-if="error" class="sp-error">{{ error }}</div>

      <template v-else>
        <div class="sp-layout">

          <!-- Left: Player -->
          <div class="sp-player">
            <div ref="termRef" class="sp-term" />

            <!-- Seek bar -->
            <div class="sp-seek-wrap">
              <div class="sp-seek-bar" @click="seekClick" ref="seekBarRef">
                <div class="sp-seek-fill" :style="{ width: seekPct + '%' }"></div>
                <div class="sp-seek-thumb" :style="{ left: seekPct + '%' }"></div>
              </div>
              <span class="sp-time">{{ fmtDuration(elapsed) }} / {{ fmtDuration(totalDuration) }}</span>
            </div>

            <!-- Controls -->
            <div class="sp-controls">
              <button class="sp-ctrl-btn" @click="rewind" title="Rewind to start">&#9664;&#9664;</button>
              <button class="sp-ctrl-btn sp-ctrl-play" @click="togglePlay">
                <span v-if="playing">&#9646;&#9646;</span>
                <span v-else>&#9654;</span>
                {{ playing ? 'Pause' : 'Play' }}
              </button>
              <div class="sp-speed">
                <span class="sp-speed-label">Speed</span>
                <div class="sp-speed-btns">
                  <button v-for="s in [0.5, 1, 2, 5, 10]" :key="s" class="sp-speed-btn"
                    :class="{ active: speed === s }" @click="setSpeed(s)">{{ s }}×</button>
                </div>
              </div>
              <div class="sp-ctrl-right">
                <span class="sp-frame-count">{{ currentFrame }} / {{ frames.length }} frames</span>
              </div>
            </div>
          </div>

          <!-- Right: Command history sidebar -->
          <div class="sp-sidebar">
            <div class="sp-sidebar-head">
              <span>Command History</span>
              <span class="sp-cmd-count">{{ commands.length }}</span>
            </div>
            <div v-if="cmdLoading" class="sp-cmd-loading">Loading…</div>
            <div v-else-if="!commands.length" class="sp-cmd-empty">No commands recorded.</div>
            <div v-else class="sp-cmd-list">
              <div
                v-for="cmd in commands"
                :key="cmd.id"
                class="sp-cmd-item"
                :class="{ 'sp-cmd-item--current': isCurrent(cmd) }"
                @click="seekToCmd(cmd)"
                :title="`${cmd.action} — click to seek`"
              >
                <div class="sp-cmd-row1">
                  <span class="sp-cmd-badge" :class="`sp-cmd-badge--${cmd.action}`">{{ cmd.action }}</span>
                  <span class="sp-cmd-time">{{ fmtTime(cmd.executed_at) }}</span>
                </div>
                <code class="sp-cmd-text">{{ cmd.command_text }}</code>
              </div>
            </div>
          </div>

        </div>
      </template>

    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import AppShell from '@/components/layout/AppShell.vue'
import api from '@/api/client'
import '@xterm/xterm/css/xterm.css'

const route = useRoute()
const id = route.params.id as string

interface Frame { t: number; d: string }
interface Command { id: string; command_text: string; action: string; executed_at: string | null }
interface RecordingMeta {
  id: string
  session_id: string
  duration_seconds: number
  created_at: string
  session_info: { host_name: string; username: string } | null
}

const loading = ref(true)
const error = ref('')
const meta = ref<RecordingMeta | null>(null)
const frames = ref<Frame[]>([])
const commands = ref<Command[]>([])
const cmdLoading = ref(false)
const termRef = ref<HTMLElement | null>(null)
const seekBarRef = ref<HTMLElement | null>(null)

const playing = ref(false)
const speed = ref(1)
const elapsed = ref(0)
const totalDuration = ref(0)
const currentFrame = ref(0)

let term: Terminal | null = null
let fitAddon: FitAddon | null = null
let rafId: number | null = null
let playStartWall = 0
let playStartOffset = 0

const seekPct = computed(() => totalDuration.value > 0 ? (elapsed.value / totalDuration.value) * 100 : 0)

function isCurrent(cmd: Command): boolean {
  if (!cmd.executed_at) return false
  if (!meta.value?.session_info) return false
  return false // approximate — commands don't have offsets, just wall-clock times
}

function fmtDuration(s: number): string {
  if (!s) return '0s'
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = Math.floor(s % 60)
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`
  return `${m}:${String(sec).padStart(2,'0')}`
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleString()
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString()
}

onMounted(async () => {
  try {
    const [metaResp, framesResp] = await Promise.all([
      api.get(`/recordings/${id}`),
      api.get(`/recordings/${id}/frames`),
    ])
    meta.value = metaResp.data
    frames.value = framesResp.data.frames ?? []
    totalDuration.value = framesResp.data.duration_seconds ?? metaResp.data.duration_seconds ?? 0

    // Load command history if session_id is available
    if (metaResp.data.session_id) {
      cmdLoading.value = true
      try {
        const cmdResp = await api.get(`/ssh/sessions/${metaResp.data.session_id}/commands`)
        commands.value = cmdResp.data
      } catch { /* non-critical */ } finally {
        cmdLoading.value = false
      }
    }
  } catch (e: any) {
    error.value = e.response?.data?.detail ?? 'Failed to load recording'
    loading.value = false
    return
  }
  loading.value = false

  await nextTick()
  await new Promise<void>(r => requestAnimationFrame(() => requestAnimationFrame(() => r())))

  if (!termRef.value) return
  term = new Terminal({
    theme: {
      background: '#0d1117', foreground: '#e6edf3',
      cursor: '#e6edf3', selectionBackground: '#3a4a5a',
      black: '#1c1e26', red: '#ff5c57', green: '#5af78e', yellow: '#f3f99d',
      blue: '#57c7ff', magenta: '#ff6ac1', cyan: '#9aedfe', white: '#f1f1f0',
    },
    fontFamily: '"Menlo", "Monaco", "Fira Code", monospace',
    fontSize: 13,
    scrollback: 5000,
    disableStdin: true,
    convertEol: false,
  })
  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.open(termRef.value)
  fitAddon.fit()

  // Auto-play on open
  if (frames.value.length > 0) {
    play()
  }
})

onBeforeUnmount(() => {
  if (rafId) cancelAnimationFrame(rafId)
  term?.dispose()
})

function togglePlay() {
  playing.value ? pause() : play()
}

function play() {
  if (!frames.value.length) return
  if (currentFrame.value >= frames.value.length) {
    rewind()
    return
  }
  playing.value = true
  playStartWall = performance.now()
  playStartOffset = elapsed.value
  scheduleNext()
}

function pause() {
  playing.value = false
  if (rafId) { cancelAnimationFrame(rafId); rafId = null }
}

function rewind() {
  pause()
  currentFrame.value = 0
  elapsed.value = 0
  term?.reset()
}

function setSpeed(s: number) {
  if (playing.value) {
    playStartWall = performance.now()
    playStartOffset = elapsed.value
  }
  speed.value = s
}

function scheduleNext() {
  if (!playing.value || currentFrame.value >= frames.value.length) {
    playing.value = false
    return
  }
  rafId = requestAnimationFrame(() => {
    const wallElapsed = (performance.now() - playStartWall) / 1000
    const videoElapsed = playStartOffset + wallElapsed * speed.value
    elapsed.value = Math.min(videoElapsed, totalDuration.value)

    while (currentFrame.value < frames.value.length && frames.value[currentFrame.value].t <= videoElapsed) {
      term?.write(frames.value[currentFrame.value].d)
      currentFrame.value++
    }

    if (currentFrame.value < frames.value.length) {
      scheduleNext()
    } else {
      playing.value = false
      elapsed.value = totalDuration.value
    }
  })
}

function seekClick(e: MouseEvent) {
  if (!seekBarRef.value || !totalDuration.value || !frames.value.length) return
  const rect = seekBarRef.value.getBoundingClientRect()
  const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
  const targetTime = pct * totalDuration.value
  seekTo(targetTime)
}

function seekToCmd(cmd: Command) {
  // Not possible to seek by command time since we don't have offsets — seek to relative position
}

function seekTo(targetTime: number) {
  const wasPlaying = playing.value
  pause()
  term?.reset()
  currentFrame.value = 0
  elapsed.value = 0

  // Replay frames up to target time without animation
  let i = 0
  while (i < frames.value.length && frames.value[i].t <= targetTime) {
    term?.write(frames.value[i].d)
    i++
  }
  currentFrame.value = i
  elapsed.value = targetTime

  if (wasPlaying) {
    playStartWall = performance.now()
    playStartOffset = targetTime
    playing.value = true
    scheduleNext()
  }
}

watch(speed, () => {
  if (playing.value) {
    playStartWall = performance.now()
    playStartOffset = elapsed.value
  }
})
</script>

<style scoped>
.sp-wrap {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: 16px 20px;
  background: var(--bg);
  color: var(--text);
  box-sizing: border-box;
}

/* ── Header ─────────────────────────────────────────────────────────────── */
.sp-header { display: flex; align-items: center; gap: 16px; margin-bottom: 12px; flex-shrink: 0; }
.sp-back { font-size: 13px; color: var(--accent2); text-decoration: none; white-space: nowrap; }
.sp-back:hover { text-decoration: underline; }
.sp-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.sp-meta-host { font-weight: 600; font-size: 14px; }
.sp-meta-sep { color: var(--border); }
.sp-meta-user { color: var(--accent); font-size: 13px; }
.sp-meta-dur  { font-family: monospace; font-size: 12px; color: var(--text2); }
.sp-meta-date { font-size: 12px; color: var(--text2); }

.sp-loading { color: var(--text2); text-align: center; padding: 40px; }
.sp-error   { color: var(--danger); padding: 16px; background: var(--bg3); border-radius: 6px; }

/* ── Two-panel layout ────────────────────────────────────────────────────── */
.sp-layout {
  display: flex;
  gap: 12px;
  flex: 1;
  min-height: 0;
}

/* Player + sidebar below are the embedded terminal-replay widget — kept
   intentionally dark like a video player's chrome, matching the live terminal
   (TermSession.vue) regardless of site theme. */
/* ── Player (left) ───────────────────────────────────────────────────────── */
.sp-player {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: #0d1117;
  border: 1px solid #21262d;
  border-radius: 8px;
  overflow: hidden;
}
.sp-term {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

/* Seek bar */
.sp-seek-wrap {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 12px; background: #161b22; border-top: 1px solid #21262d;
}
.sp-seek-bar {
  flex: 1; height: 4px; background: #30363d; border-radius: 2px;
  position: relative; cursor: pointer;
}
.sp-seek-bar:hover .sp-seek-thumb { opacity: 1; }
.sp-seek-fill { height: 100%; background: #58a6ff; border-radius: 2px; pointer-events: none; }
.sp-seek-thumb {
  position: absolute; top: 50%; transform: translate(-50%, -50%);
  width: 12px; height: 12px; background: #58a6ff; border-radius: 50%;
  opacity: 0; transition: opacity 0.15s; pointer-events: none;
}
.sp-time { font-family: monospace; font-size: 12px; color: #8b949e; white-space: nowrap; }

/* Controls */
.sp-controls {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 8px 12px; background: #161b22; border-top: 1px solid #21262d;
}
.sp-ctrl-btn {
  padding: 4px 10px; border-radius: 5px; font-size: 12px; font-weight: 500;
  border: 1px solid #30363d; background: #21262d; color: #8b949e;
  cursor: pointer; display: inline-flex; align-items: center; gap: 4px;
  transition: color 0.15s, border-color 0.15s;
}
.sp-ctrl-btn:hover { border-color: #8b949e; color: #e6edf3; }
.sp-ctrl-play { border-color: #1f3a5f; color: #58a6ff; background: #162032; }
.sp-ctrl-play:hover { border-color: #58a6ff; background: #1a2a40; }
.sp-speed { display: flex; align-items: center; gap: 6px; }
.sp-speed-label { font-size: 11px; color: #484f58; text-transform: uppercase; letter-spacing: 0.06em; }
.sp-speed-btns { display: flex; gap: 3px; }
.sp-speed-btn {
  padding: 2px 6px; border-radius: 3px; font-size: 11px; font-weight: 500;
  border: 1px solid #30363d; background: transparent; color: #484f58;
  cursor: pointer; transition: all 0.12s;
}
.sp-speed-btn.active { background: #1f3a5f; border-color: #58a6ff; color: #58a6ff; }
.sp-speed-btn:not(.active):hover { border-color: #8b949e; color: #8b949e; }
.sp-ctrl-right { margin-left: auto; }
.sp-frame-count { font-size: 11px; color: #30363d; font-family: monospace; }

/* ── Command history sidebar (right) ─────────────────────────────────────── */
.sp-sidebar {
  width: 280px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: #0d1117;
  border: 1px solid #21262d;
  border-radius: 8px;
  overflow: hidden;
}
.sp-sidebar-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px;
  font-size: 11px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.06em; color: #484f58;
  border-bottom: 1px solid #21262d; background: #161b22; flex-shrink: 0;
}
.sp-cmd-count {
  background: #21262d; color: #8b949e; font-size: 11px;
  padding: 0 5px; border-radius: 8px;
}
.sp-cmd-loading, .sp-cmd-empty {
  padding: 20px 14px; font-size: 12px; color: #484f58; text-align: center;
}
.sp-cmd-list {
  flex: 1; overflow-y: auto; padding: 6px;
}
.sp-cmd-list::-webkit-scrollbar { width: 4px; }
.sp-cmd-list::-webkit-scrollbar-track { background: transparent; }
.sp-cmd-list::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 2px; }

.sp-cmd-item {
  padding: 7px 8px; border-radius: 5px; margin-bottom: 3px;
  cursor: pointer; transition: background 0.12s;
  background: #0d1117;
}
.sp-cmd-item:hover { background: #161b22; }
.sp-cmd-item--current { background: #162032; border-left: 2px solid #58a6ff; }

.sp-cmd-row1 { display: flex; align-items: center; justify-content: space-between; margin-bottom: 3px; }
.sp-cmd-badge {
  padding: 1px 5px; border-radius: 3px; font-size: 9px;
  font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em;
}
.sp-cmd-badge--logged  { background: #1c2128; color: #484f58; }
.sp-cmd-badge--allow   { background: #1f3d2f; color: #3fb950; }
.sp-cmd-badge--deny    { background: #2d1a1a; color: #f85149; }
.sp-cmd-badge--confirm { background: #2d2000; color: #e3b341; }
.sp-cmd-time { font-size: 10px; color: #30363d; font-family: monospace; }
.sp-cmd-text {
  display: block; font-family: 'Menlo', 'Monaco', monospace; font-size: 11px;
  color: #9aedfe; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

:deep(.xterm) { width: 100%; height: 100%; }
:deep(.xterm-screen) { display: block; }
:deep(.xterm-viewport)::-webkit-scrollbar { width: 6px; }
:deep(.xterm-viewport)::-webkit-scrollbar-track { background: transparent; }
:deep(.xterm-viewport)::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.12); border-radius: 3px; }
</style>
