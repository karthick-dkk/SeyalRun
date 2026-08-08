<template>
  <div class="zbx-run">
    <div class="card">
      <div class="card-header">Run remediation from Zabbix</div>
      <div style="padding:20px">
        <div v-if="loading" style="color:var(--text2)">Resolving problem…</div>

        <template v-else>
          <div class="zr-row"><span class="zr-k">Host</span><span class="zr-v">{{ host?.name || zbxHost || '—' }}</span></div>
          <div class="zr-row" v-if="eventId"><span class="zr-k">Zabbix event</span><span class="zr-v mono">{{ eventId }}</span></div>
          <div class="zr-row" v-if="triggerId"><span class="zr-k">Trigger</span><span class="zr-v mono">{{ triggerId }}</span></div>

          <div v-if="error" class="zr-error">{{ error }}</div>

          <template v-if="bindings.length">
            <div class="zr-label">Bound playbook</div>
            <div class="zr-toggle-group">
              <button v-for="b in bindings" :key="b.id"
                      :class="['zr-toggle', selected?.id === b.id && 'active']" @click="selected = b">
                {{ b.name }}
              </button>
            </div>
            <div v-if="!host" class="zr-error">This Zabbix host isn't linked to a SeyalRun host — sync hosts first.</div>
            <button class="btn btn-primary" style="margin-top:16px"
                    :disabled="running || !selected || !host" @click="run">
              {{ running ? 'Starting…' : '▶ Run remediation' }}
            </button>
            <div style="font-size:12px;color:var(--text2);margin-top:8px">
              Output streams live below and is posted back onto the Zabbix Problem.
            </div>
          </template>
          <div v-else-if="!error" class="zr-empty">No enabled trigger binding matches this problem. Bind a playbook in Admin → Zabbix Integration.</div>
        </template>

        <router-link v-if="startedRunId" :to="`/jobs/${startedRunId}`" class="btn" style="margin-top:16px;display:inline-block">
          Open full job run →
        </router-link>
      </div>
    </div>

    <div v-if="startedRunId" class="card" style="margin-top:16px">
      <div class="card-header">Output</div>
      <pre class="zr-out">{{ outputText || '(waiting for output…)' }}</pre>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import api from '@/api/client'

const route = useRoute()
const zbxHost = route.query.zbx_host as string | undefined      // Zabbix hostid ({HOST.ID})
const eventId = route.query.event as string | undefined         // {EVENT.ID}
const triggerId = route.query.trigger as string | undefined     // {TRIGGER.ID}
const severity = Number(route.query.severity || 0)

const loading = ref(true)
const running = ref(false)
const error = ref('')
const host = ref<any>(null)
const bindings = ref<any[]>([])
const selected = ref<any>(null)
const startedRunId = ref('')
const outputText = ref('')
let poll: ReturnType<typeof setInterval> | null = null

async function resolve() {
  try {
    // Match the Zabbix host to a SeyalRun host by its zabbix_hostid.
    if (zbxHost) {
      const hosts = await api.get('/hosts').then(r => r.data).catch(() => [])
      host.value = (hosts || []).find((h: any) => String(h.zabbix_hostid) === String(zbxHost)) || null
    }
    const params: any = {}
    if (triggerId) params.triggerid = triggerId
    if (severity) params.severity = severity
    bindings.value = await api.get('/triggers/resolve', { params }).then(r => r.data).catch(() => [])
    if (bindings.value.length) selected.value = bindings.value[0]
  } catch (e: any) {
    error.value = e?.response?.data?.detail || 'Failed to resolve problem'
  } finally {
    loading.value = false
  }
}

async function run() {
  if (!selected.value || !host.value) return
  running.value = true; error.value = ''
  try {
    const { data } = await api.post('/triggers/manual', {
      binding_id: selected.value.id,
      host_id: host.value.id,
      zbx_event_id: eventId || null,
    })
    startedRunId.value = data.run_id
    poll = setInterval(loadOutput, 2000)
    loadOutput()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || 'Failed to start run'
  } finally {
    running.value = false
  }
}

async function loadOutput() {
  if (!startedRunId.value) return
  try {
    const { data } = await api.get(`/job-runs/${startedRunId.value}`)
    outputText.value = (data.output_lines || []).join('\n')
    if (['success', 'failed', 'error', 'cancelled'].includes(data.status) && poll) {
      clearInterval(poll); poll = null
    }
  } catch { /* keep polling */ }
}

onMounted(resolve)
onBeforeUnmount(() => { if (poll) clearInterval(poll) })
</script>

<style scoped>
.zbx-run { max-width: 640px; margin: 24px auto; }
.zr-row { display: flex; gap: 12px; padding: 6px 0; border-bottom: 1px solid var(--border); }
.zr-k { min-width: 110px; color: var(--text2); font-size: 13px; }
.zr-v { color: var(--text); font-size: 13px; }
.mono { font-family: monospace; }
.zr-label { font-size: 12px; color: var(--text2); margin: 16px 0 6px; }
.zr-toggle-group { display: flex; flex-wrap: wrap; gap: 6px; }
.zr-toggle { padding: 7px 12px; font-size: 13px; background: var(--bg3); border: 1px solid var(--border); border-radius: 6px; color: var(--text2); cursor: pointer; }
.zr-toggle.active { background: var(--bg2); color: var(--text); border-color: var(--accent2); }
.zr-error { color: var(--danger); font-size: 13px; margin-top: 12px; }
.zr-empty { color: var(--text2); font-size: 13px; margin-top: 12px; }
/* Raw playbook/webhook output — kept intentionally dark like a terminal/log
   console, matching JobRunView.vue's own output log regardless of site theme. */
.zr-out { background: #0d1117; color: #c9d1d9; padding: 14px; border-radius: 8px; max-height: 420px; overflow: auto; font-size: 12px; white-space: pre-wrap; margin: 0; }
</style>
