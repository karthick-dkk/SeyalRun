<template>
  <div class="card mar">
    <div class="card-header">
      <span>My access requests <span v-if="pendingCount" class="badge badge-yellow">{{ pendingCount }} pending</span></span>
      <button class="btn btn-sm" type="button" title="Refresh" @click="load">↺</button>
    </div>
    <div v-if="error" class="mar-error" role="alert">{{ error }}</div>
    <div v-if="!items.length" class="empty-cell-sm">
      {{ loading ? 'Loading…' : 'No requests yet. Use “Request access” on a host you can see but cannot log in to.' }}
    </div>
    <table v-else class="table">
      <thead>
        <tr><th>Request</th><th>Reason</th><th>Duration</th><th>Status</th><th>Requested</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="r in items" :key="r.id">
          <td>{{ label(r) }}</td>
          <td class="mar-reason" :title="r.reason || ''">{{ r.reason || '—' }}</td>
          <td class="text-muted-sm">{{ fmtDuration(r.duration_minutes) }}</td>
          <td>
            <span class="badge" :class="badge(r.status)">{{ statusLabel(r.status) }}</span>
            <div v-if="r.status === 'active' && r.date_expired" class="text-muted-sm">until {{ fmt(r.date_expired) }}</div>
          </td>
          <td class="text-muted-sm">{{ fmt(r.created_at) }}</td>
          <td>
            <button v-if="r.status === 'pending_approval'" class="btn btn-sm" :disabled="busy === r.id" @click="cancel(r)">Cancel</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import api from '@/api/client'

interface AccessRequest {
  id: string; name: string; host_id: string | null; credential_id: string | null; reason: string | null
  duration_minutes: number | null; status: string; created_at: string | null; date_expired: string | null
}

const props = defineProps<{ hostNames?: Record<string, string> }>()

const items = ref<AccessRequest[]>([])
const loading = ref(false)
const error = ref('')
const busy = ref('')
const pendingCount = computed(() => items.value.filter(r => r.status === 'pending_approval').length)

const STATUS: Record<string, [string, string]> = {
  pending_approval: ['Pending', 'badge-yellow'],
  active: ['Approved', 'badge-green'],
  rejected: ['Rejected', 'badge-red'],
  expired: ['Expired', 'badge-gray'],
  cancelled: ['Cancelled', 'badge-gray'],
}
const statusLabel = (s: string) => STATUS[s]?.[0] ?? s
const badge = (s: string) => STATUS[s]?.[1] ?? 'badge-gray'

function label(r: AccessRequest): string {
  const host = r.host_id && props.hostNames?.[r.host_id]
  // The server-built name already says host and account; prefer it verbatim.
  return r.name?.replace(/^Access request: /, '') || host || r.host_id || '—'
}
function fmt(iso: string | null): string { return iso ? new Date(iso).toLocaleString() : '—' }
function fmtDuration(m: number | null): string {
  if (!m) return '—'
  if (m % 1440 === 0) return `${m / 1440} day${m === 1440 ? '' : 's'}`
  if (m % 60 === 0) return `${m / 60} hour${m === 60 ? '' : 's'}`
  return `${m} min`
}

async function load() {
  loading.value = true
  error.value = ''
  try { items.value = (await api.get('/auth/access-requests')).data?.items || [] }
  catch (e: any) { error.value = e?.response?.data?.detail || 'Could not load your access requests.' }
  finally { loading.value = false }
}
async function cancel(r: AccessRequest) {
  busy.value = r.id
  error.value = ''
  try { await api.post(`/auth/access-requests/${r.id}/cancel`); await load() }
  catch (e: any) { error.value = e?.response?.data?.detail || 'Cancel failed.' }
  finally { busy.value = '' }
}

defineExpose({ load })
onMounted(load)
</script>

<style scoped>
.mar { margin-top: 20px; }
.mar-error { color: var(--danger); font-size: 12.5px; padding: 8px 16px 0; }
.mar-reason { max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
