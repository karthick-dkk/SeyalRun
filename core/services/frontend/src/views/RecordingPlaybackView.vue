<template>
  <AppShell>
    <div class="view-wrap">
      <div class="view-header">
        <router-link to="/recordings" class="back-link">&#8592; Recordings</router-link>
        <h1>Playback</h1>
        <div v-if="meta" class="playback-meta">
          {{ meta.session_info?.host_name }} &bull; {{ meta.session_info?.username }} &bull; {{ formatDuration(meta.duration_seconds) }}
        </div>
      </div>

      <div v-if="loading" class="loading">Loading…</div>
      <div v-else-if="error" class="error-msg">{{ error }}</div>
      <template v-else>
        <!-- xterm playback container -->
        <div class="player-wrap">
          <div ref="termRef" class="player-term" />
        </div>

        <!-- Controls -->
        <div class="player-controls">
          <button class="btn btn-sm" @click="rewind">&#9664;&#9664; Rewind</button>
          <button class="btn btn-sm btn-primary" @click="togglePlay">
            {{ playing ? '&#9646;&#9646; Pause' : '&#9654; Play' }}
          </button>
          <label class="speed-label">
            Speed
            <select v-model="speed" class="speed-select">
              <option :value="0.5">0.5×</option>
              <option :value="1">1×</option>
              <option :value="2">2×</option>
              <option :value="4">4×</option>
              <option :value="10">10×</option>
            </select>
          </label>
          <span class="progress-label">{{ formatDuration(elapsed) }} / {{ formatDuration(totalDuration) }}</span>
        </div>
      </template>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import AppShell from '@/components/layout/AppShell.vue'
import api from '@/api/client'

import '@xterm/xterm/css/xterm.css'

const route = useRoute()
const id = route.params.id as string

interface Frame { t: number; d: string }
interface RecordingMeta {
  duration_seconds: number
  session_info: { host_name: string; username: string } | null
}

const loading = ref(true)
const error = ref('')
const meta = ref<RecordingMeta | null>(null)
const frames = ref<Frame[]>([])
const termRef = ref<HTMLElement | null>(null)
const playing = ref(false)
const speed = ref(1)
const elapsed = ref(0)
const totalDuration = ref(0)

let term: Terminal | null = null
let fitAddon: FitAddon | null = null
let rafId: number | null = null
let frameIdx = 0
let playStartWall = 0
let playStartOffset = 0

onMounted(async () => {
  try {
    const [metaResp, framesResp] = await Promise.all([
      api.get(`/recordings/${id}`),
      api.get(`/recordings/${id}/frames`),
    ])
    meta.value = metaResp.data
    frames.value = framesResp.data.frames ?? []
    totalDuration.value = framesResp.data.duration_seconds ?? metaResp.data.duration_seconds
  } catch (err: any) {
    error.value = err.response?.data?.detail ?? 'Failed to load recording'
    loading.value = false
    return
  }
  loading.value = false

  await new Promise((r) => requestAnimationFrame(r))
  if (!termRef.value) return

  term = new Terminal({
    theme: { background: '#0d1117', foreground: '#e6edf3' },
    fontFamily: '"Cascadia Code", "Fira Code", monospace',
    fontSize: 14,
    scrollback: 5000,
    disableStdin: true,
  })
  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.open(termRef.value)
  fitAddon.fit()
})

onBeforeUnmount(() => {
  if (rafId) cancelAnimationFrame(rafId)
  term?.dispose()
})

function togglePlay() {
  if (playing.value) {
    pause()
  } else {
    play()
  }
}

function play() {
  if (frames.value.length === 0) return
  playing.value = true
  playStartWall = performance.now()
  playStartOffset = elapsed.value
  scheduleNext()
}

function pause() {
  playing.value = false
  if (rafId) {
    cancelAnimationFrame(rafId)
    rafId = null
  }
}

function rewind() {
  pause()
  frameIdx = 0
  elapsed.value = 0
  term?.reset()
}

function scheduleNext() {
  if (!playing.value || frameIdx >= frames.value.length) {
    playing.value = false
    return
  }
  rafId = requestAnimationFrame(() => {
    const wallElapsed = (performance.now() - playStartWall) / 1000
    const videoElapsed = playStartOffset + wallElapsed * speed.value
    elapsed.value = Math.min(videoElapsed, totalDuration.value)

    while (frameIdx < frames.value.length && frames.value[frameIdx].t <= videoElapsed) {
      term?.write(frames.value[frameIdx].d)
      frameIdx++
    }

    if (frameIdx < frames.value.length) {
      scheduleNext()
    } else {
      playing.value = false
      elapsed.value = totalDuration.value
    }
  })
}

watch(speed, () => {
  if (playing.value) {
    // Re-anchor start wall so speed change doesn't jump
    playStartWall = performance.now()
    playStartOffset = elapsed.value
  }
})

function formatDuration(s: number) {
  if (!s) return '0s'
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`
}
</script>

<style scoped>
.back-link {
  font-size: 13px;
  color: var(--accent2);
  text-decoration: none;
  display: inline-block;
  margin-bottom: 6px;
}
.playback-meta {
  font-size: 13px;
  color: var(--text2);
  margin-top: 4px;
}
/* player-wrap/controls below are the embedded terminal-replay widget — kept
   intentionally dark like a video player's chrome, matching the live terminal
   (TermSession.vue) and other terminal-replay views regardless of site theme. */
.player-wrap {
  background: #0d1117;
  border-radius: 6px 6px 0 0;
  min-height: 400px;
  overflow: hidden;
}
.player-term {
  height: 400px;
}
.player-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  background: var(--color-surface, #161b22);
  border-radius: 0 0 6px 6px;
  border-top: 1px solid var(--color-border, #30363d);
}
.speed-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}
.speed-select {
  background: var(--color-input, #0d1117);
  color: inherit;
  border: 1px solid var(--color-border, #30363d);
  border-radius: 4px;
  padding: 2px 6px;
  font-size: 13px;
}
.progress-label {
  margin-left: auto;
  font-size: 13px;
  font-family: monospace;
  color: var(--color-text-muted, #8b949e);
}
</style>
