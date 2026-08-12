<template>
  <AppShell>
    <div class="settings-page">
      <h1 class="settings-title">Settings</h1>

      <!-- SSH Session Settings -->
      <div class="settings-section">
        <h2>SSH Session Persistence</h2>
        <p class="text-muted">
          When you navigate away from the Terminal page, active SSH sessions stay alive in the background.
          They are automatically closed after the idle timeout.
        </p>
        <div class="setting-row">
          <label class="setting-label">
            Idle Timeout
            <span class="setting-hint">How long to keep disconnected sessions alive</span>
          </label>
          <div class="setting-control">
            <input
              type="number"
              v-model.number="idleTimeout"
              min="1" max="480" step="1"
              class="num-input"
              @change="saveSettings"
            />
            <span class="unit">minutes</span>
          </div>
        </div>
        <div class="setting-presets">
          <button v-for="p in presets" :key="p.val"
            class="preset-btn" :class="{ active: idleTimeout === p.val }"
            @click="idleTimeout = p.val; saveSettings()">
            {{ p.label }}
          </button>
        </div>
        <p v-if="settingsSaved" class="text-success save-msg">✓ Saved</p>
      </div>

      <!-- Studio status card (only here, not in TopBar) -->
      <div class="settings-section status-section">
        <div class="status-row">
          <div>
            <h2>Studio Status</h2>
            <p class="text-muted">Playbook Studio API on port 8005</p>
          </div>
          <div class="status-indicator">
            <span class="status-dot-lg" :class="studioOk === true ? 'dot-ok' : studioOk === false ? 'dot-err' : 'dot-idle'" />
            <span class="status-label" :class="studioOk === true ? 'text-success' : studioOk === false ? 'text-error' : 'text-muted'">
              {{ studioOk === true ? 'Studio online' : studioOk === false ? 'Studio offline' : 'Checking…' }}
            </span>
          </div>
        </div>
        <button class="btn-secondary" @click="testStudio">↺ Check now</button>
        <p v-if="studioStatus" :class="studioOk ? 'text-success' : 'text-error'" class="status-msg">{{ studioStatus }}</p>
      </div>

      <div class="settings-section">
        <h2>JumpServer Connection</h2>
        <p class="text-muted">API endpoint: <code class="mono">http://192.168.64.2</code></p>
        <button class="btn-secondary" @click="testJms">Test Connection</button>
        <p v-if="jmsStatus" :class="jmsOk ? 'text-success' : 'text-error'">{{ jmsStatus }}</p>
      </div>

      <div class="settings-section">
        <h2>About</h2>
        <div class="about-info">
          <div><span class="label">Product</span><span>SeyalRun Console</span></div>
          <div><span class="label">Version</span><span>1.0.0</span></div>
          <div><span class="label">Build</span><span class="mono">seyalrun-console</span></div>
        </div>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import axios from 'axios'
import AppShell from '@/components/layout/AppShell.vue'
import { useAuthStore } from '@/stores/auth'
import { api } from '@/api/client'

const auth = useAuthStore()
const jmsStatus = ref('')
const jmsOk = ref(false)
const studioOk = ref<boolean | null>(null)
const studioStatus = ref('')

// SSH idle timeout setting
const idleTimeout = ref(15)
const settingsSaved = ref(false)
const presets = [
  { label: '5 min', val: 5 },
  { label: '15 min', val: 15 },
  { label: '30 min', val: 30 },
  { label: '1 hour', val: 60 },
]

async function loadSettings() {
  try {
    const resp = await api.get('/settings')
    idleTimeout.value = resp.data.ssh_idle_timeout_minutes ?? 15
  } catch { /* ignore */ }
}

async function saveSettings() {
  try {
    await api.patch('/settings', { ssh_idle_timeout_minutes: idleTimeout.value })
    settingsSaved.value = true
    setTimeout(() => { settingsSaved.value = false }, 2000)
  } catch { /* ignore */ }
}

async function testJms() {
  try {
    await axios.get('/api/v1/me', { headers: { Authorization: `Bearer ${auth.token}` } })
    jmsOk.value = true
    jmsStatus.value = '✓ JumpServer reachable and authenticated'
  } catch {
    jmsOk.value = false
    jmsStatus.value = '✗ Connection failed'
  }
}

async function testStudio() {
  studioOk.value = null
  studioStatus.value = ''
  try {
    const resp = await axios.get('/api/v1/health')
    studioOk.value = resp.data?.status === 'ok'
    studioStatus.value = studioOk.value ? '✓ Playbook Studio is healthy' : '✗ Studio returned unexpected response'
  } catch {
    studioOk.value = false
    studioStatus.value = '✗ Studio unreachable on port 8005'
  }
}

onMounted(() => { testStudio(); loadSettings() })
</script>

<style scoped>
.settings-page { display: flex; flex-direction: column; gap: var(--space-6); max-width: 600px; }
.settings-title { font-size: 20px; font-weight: 700; }
.settings-section { background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: var(--space-5); display: flex; flex-direction: column; gap: var(--space-3); }
.settings-section h2 { font-size: 14px; font-weight: 600; }

.status-section { border-color: var(--border-muted); }
.status-row { display: flex; justify-content: space-between; align-items: center; }
.status-indicator { display: flex; align-items: center; gap: var(--space-2); }
.status-dot-lg { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.dot-ok   { background: var(--success); box-shadow: 0 0 8px var(--success); }
.dot-err  { background: var(--error); }
.dot-idle { background: var(--text-subtle); animation: pulse 1.5s infinite; }
@keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:.3 } }
.status-label { font-size: 13px; font-weight: 600; font-family: var(--font-mono); }
.status-msg { font-size: 12px; }

.btn-secondary { background: none; border: 1px solid var(--border); color: var(--text-muted); border-radius: var(--radius-md); padding: var(--space-2) var(--space-4); font-size: 13px; cursor: pointer; transition: all var(--transition); width: fit-content; }
.btn-secondary:hover { color: var(--text); border-color: var(--border-muted); }
.mono { font-family: var(--font-mono); }
.text-muted { color: var(--text-muted); }
.text-success { color: var(--success); }
.text-error { color: var(--error); }
.about-info { display: flex; flex-direction: column; gap: var(--space-2); font-size: 13px; }
.about-info div { display: flex; gap: var(--space-4); }
.label { color: var(--text-muted); width: 80px; }

/* SSH settings */
.setting-row { display: flex; justify-content: space-between; align-items: flex-start; gap: var(--space-4); }
.setting-label { font-size: 13px; font-weight: 500; display: flex; flex-direction: column; gap: 3px; }
.setting-hint { font-size: 11px; color: var(--text-muted); font-weight: 400; }
.setting-control { display: flex; align-items: center; gap: var(--space-2); flex-shrink: 0; }
.num-input { width: 72px; background: var(--bg-base); border: 1px solid var(--border); border-radius: var(--radius-md); color: var(--text); font-size: 14px; font-family: var(--font-mono); padding: var(--space-2) var(--space-3); outline: none; text-align: center; }
.num-input:focus { border-color: var(--accent); }
.unit { font-size: 12px; color: var(--text-muted); }
.setting-presets { display: flex; gap: var(--space-2); flex-wrap: wrap; }
.preset-btn { padding: var(--space-1) var(--space-3); border: 1px solid var(--border); border-radius: var(--radius-full); background: none; color: var(--text-muted); font-size: 12px; cursor: pointer; transition: all var(--transition); }
.preset-btn.active, .preset-btn:hover { border-color: var(--accent); color: var(--accent); background: var(--accent-dim); }
.save-msg { font-size: 12px; }
</style>
