<template>
  <AppShell>
    <div class="page">
      <div class="page-header">
        <div>
          <div class="page-title">Job Runs</div>
          <div class="page-subtitle">Automation job history and live status</div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          Recent Job Runs
          <button class="btn btn-sm" @click="load">Refresh</button>
        </div>
        <table class="table">
          <thead>
            <tr>
              <th>Template</th><th>Action</th><th>Triggered By</th>
              <th>Hosts</th><th>Login</th>
              <th>Status</th><th>Started</th><th>Duration</th><th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="run in runs" :key="run.id" style="cursor:pointer" @click="router.push(`/jobs/${run.id}`)">
              <td style="font-weight:600">{{ run.job_template_name || run.job_template_id }}</td>
              <td><span class="badge badge-gray" style="font-size:11px">{{ run.action_type || '—' }}</span></td>
              <td style="font-size:12px;color:var(--text2)">{{ triggeredByLabel(run) }}</td>
              <td style="font-size:12px;color:var(--text2)" :title="hostsTitle(run)">{{ hostsLabel(run) }}</td>
              <td style="font-size:12px;color:var(--text2)">{{ credentialLabel(run) }}</td>
              <td><span :class="statusClass(run.status)">{{ run.status }}</span></td>
              <td style="color:var(--text2);font-size:12px">{{ run.started_at ? new Date(run.started_at).toLocaleString() : '—' }}</td>
              <td style="color:var(--text2);font-size:12px">{{ duration(run) }}</td>
              <td>
                <button class="btn-pill btn-pill-outline" @click.stop="router.push(`/jobs/${run.id}`)">View</button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="!runs.length && !loading" style="padding:24px;text-align:center;color:var(--text2)">No job runs yet.</div>
        <div v-if="loading" style="padding:24px;text-align:center;color:var(--text2)">Loading…</div>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'
import api from '@/api/client'

const router = useRouter()
const runs = ref<any[]>([])
const loading = ref(false)

// Best-effort name lookups — each may be empty for non-admins (credentials/users are
// admin-gated at the gateway); we degrade to showing the raw id rather than failing.
const hostMap = ref<Record<string, string>>({})
const credMap = ref<Record<string, string>>({})
const userMap = ref<Record<string, string>>({})

async function load() {
  loading.value = true
  try {
    const [runsRes, hosts, creds, users] = await Promise.all([
      api.get('/job-runs'),
      api.get('/hosts').then(r => r.data).catch(() => []),
      api.get('/credentials').then(r => r.data).catch(() => []),
      api.get('/users').then(r => r.data).catch(() => []),
    ])
    runs.value = runsRes.data
    hostMap.value = Object.fromEntries(hosts.map((h: any) => [h.id, h.name]))
    credMap.value = Object.fromEntries(creds.map((c: any) => [c.id, c.name]))
    userMap.value = Object.fromEntries(users.map((u: any) => [u.id, u.username || u.name || u.id]))
  } finally {
    loading.value = false
  }
}
onMounted(load)

function triggeredByLabel(run: any): string {
  if (run.triggered_by_kind === 'user' && run.triggered_by_user_id) {
    return '👤 ' + (userMap.value[run.triggered_by_user_id] || run.triggered_by_user_id)
  }
  const tb = run.triggered_by || ''
  if (tb.startsWith('schedule:')) return '📅 Schedule'
  if (tb.startsWith('zabbix_event:')) return '🔔 Zabbix'
  if (tb.startsWith('manual_trigger:')) return '🖱 Manual'
  return tb || '—'
}

function hostsLabel(run: any): string {
  const ids: string[] = run.target_host_ids || []
  if (!ids.length) return '—'
  const names = ids.map(id => hostMap.value[id] || id)
  if (names.length <= 2) return names.join(', ')
  return `${names[0]}, ${names[1]} +${names.length - 2}`
}
function hostsTitle(run: any): string {
  return (run.target_host_ids || []).map((id: string) => hostMap.value[id] || id).join(', ')
}

function credentialLabel(run: any): string {
  const hc = run.host_credentials || {}
  if (Object.keys(hc).length) return 'Per-host'
  const cid = run.credential_id
  if (!cid) return 'Default (push acct)'
  return credMap.value[cid] || cid
}

function statusClass(s: string) {
  if (s === 'success') return 'badge badge-green'
  if (s === 'failed' || s === 'error' || s === 'rejected') return 'badge badge-red'
  if (s === 'running') return 'badge badge-blue'
  if (s === 'pending_approval') return 'badge badge-yellow'
  return 'badge badge-gray'
}

function duration(run: any): string {
  if (!run.started_at) return '—'
  const end = run.ended_at ? new Date(run.ended_at) : new Date()
  const secs = Math.round((end.getTime() - new Date(run.started_at).getTime()) / 1000)
  if (secs < 60) return `${secs}s`
  return `${Math.floor(secs / 60)}m ${secs % 60}s`
}
</script>
