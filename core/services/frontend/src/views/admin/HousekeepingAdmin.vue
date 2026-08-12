<template>
  <div>
    <div class="card">
      <div class="card-header">
        Housekeeping Jobs
        <button class="btn btn-sm btn-icon" @click="load" title="Refresh">↻</button>
      </div>
      <table class="table">
        <thead>
          <tr><th>Job</th><th>Schedule</th><th>Last Run</th><th>Status</th><th>Next Run</th><th>Enabled</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="j in jobs" :key="j.job_key">
            <td>
              <div style="font-weight:600">{{ j.display_name }}</div>
              <div style="font-size:11px;color:var(--text2)">{{ j.description }}</div>
            </td>
            <td>
              <input
                class="hk-cron"
                :value="j.cron_override || j.cron_expression"
                :placeholder="j.cron_expression"
                @change="setCron(j, ($event.target as HTMLInputElement).value)"
                :title="j.cron_override ? 'Override (default: ' + j.cron_expression + ')' : 'Default schedule'"
              />
            </td>
            <td style="font-size:12px;color:var(--text2)">{{ fmtDate(j.last_run_at) }}</td>
            <td>
              <span v-if="j.last_run_status === 'success'" class="badge badge-green">OK</span>
              <span v-else-if="j.last_run_status === 'error'" class="badge badge-red" :title="j.last_run_error">Error</span>
              <span v-else-if="j.last_run_status === 'running'" class="badge badge-yellow">Running</span>
              <span v-else style="color:var(--text2)">—</span>
            </td>
            <td style="font-size:12px;color:var(--text2)">{{ fmtDate(j.next_run_at) }}</td>
            <td>
              <label class="hk-switch">
                <input type="checkbox" :checked="j.enabled" @change="toggle(j)" />
                <span class="hk-slider"></span>
              </label>
            </td>
            <td>
              <div style="display:flex;gap:6px;justify-content:flex-end">
                <button class="btn-pill btn-pill-outline" :disabled="j.manual_trigger" @click="trigger(j)">
                  {{ j.manual_trigger ? 'Queued…' : '▶ Run now' }}
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!jobs.length && !loading" style="padding:32px;text-align:center;color:var(--text2)">No housekeeping jobs.</div>
      <div v-if="loading" style="padding:32px;text-align:center;color:var(--text2)">Loading…</div>
    </div>
    <div v-if="error" style="color:var(--danger);font-size:12px;margin-top:8px">{{ error }}</div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api/client'

const jobs = ref<any[]>([])
const loading = ref(false)
const error = ref('')

function fmtDate(iso?: string | null) { return iso ? new Date(iso).toLocaleString() : '—' }

async function load() {
  loading.value = true; error.value = ''
  try { const { data } = await api.get('/housekeeping/jobs'); jobs.value = data }
  catch (e: any) { error.value = e?.response?.data?.detail || 'Failed to load jobs' }
  finally { loading.value = false }
}
async function toggle(j: any) {
  try { await api.put(`/housekeeping/jobs/${j.job_key}`, { enabled: !j.enabled }); load() }
  catch (e: any) { error.value = e?.response?.data?.detail || 'Failed' }
}
async function setCron(j: any, value: string) {
  const override = value.trim() === j.cron_expression ? '' : value.trim()
  try { await api.put(`/housekeeping/jobs/${j.job_key}`, { cron_override: override }); load() }
  catch (e: any) { error.value = e?.response?.data?.detail || 'Failed' }
}
async function trigger(j: any) {
  try { await api.post(`/housekeeping/jobs/${j.job_key}/trigger`); j.manual_trigger = true; setTimeout(load, 2000) }
  catch (e: any) { error.value = e?.response?.data?.detail || 'Failed' }
}

onMounted(load)
</script>

<style scoped>
.hk-cron {
  width: 130px; padding: 5px 8px; background: var(--bg3); border: 1px solid var(--border);
  border-radius: 5px; color: var(--text); font-size: 12px; font-family: var(--font-mono, monospace); outline: none;
}
.hk-cron:focus { border-color: var(--accent2); }
.hk-switch { position: relative; display: inline-block; width: 36px; height: 20px; }
.hk-switch input { opacity: 0; width: 0; height: 0; }
.hk-slider { position: absolute; cursor: pointer; inset: 0; background: var(--border); border-radius: 20px; transition: 0.2s; }
.hk-slider::before { content: ""; position: absolute; height: 14px; width: 14px; left: 3px; bottom: 3px; background: var(--text2); border-radius: 50%; transition: 0.2s; }
.hk-switch input:checked + .hk-slider { background: rgba(34,197,94,0.4); }
.hk-switch input:checked + .hk-slider::before { transform: translateX(16px); background: #4ade80; }
</style>
