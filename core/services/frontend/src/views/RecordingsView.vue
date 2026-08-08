<template>
  <AppShell>
    <div class="view-wrap">
      <div class="view-header">
        <h1>Session Recordings</h1>
      </div>

      <div v-if="loading" class="loading">Loading…</div>
      <div v-else-if="error" class="error-msg">{{ error }}</div>
      <div v-else-if="recordings.length === 0" class="empty-state">No recordings yet.</div>

      <table v-else class="data-table">
        <thead>
          <tr>
            <th>Host</th>
            <th>User</th>
            <th>Started</th>
            <th>Duration</th>
            <th>Size</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in recordings" :key="r.id">
            <td>{{ r.session_info?.host_name ?? '—' }}</td>
            <td>{{ r.session_info?.username ?? '—' }}</td>
            <td>{{ formatDate(r.created_at) }}</td>
            <td>{{ formatDuration(r.duration_seconds) }}</td>
            <td>{{ formatSize(r.size_bytes) }}</td>
            <td>
              <router-link v-if="r.format !== 'purged'" :to="`/recordings/${r.id}`" class="btn btn-sm">
                Play
              </router-link>
              <span v-else class="badge badge-muted">purged</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import AppShell from '@/components/layout/AppShell.vue'
import api from '@/api/client'

interface Recording {
  id: string
  session_id: string
  format: string
  duration_seconds: number
  size_bytes: number
  created_at: string
  session_info: { host_name: string; username: string; user_id: string } | null
}

const recordings = ref<Recording[]>([])
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  try {
    const resp = await api.get('/recordings')
    recordings.value = resp.data
  } catch (err: any) {
    error.value = err.response?.data?.detail ?? 'Failed to load recordings'
  } finally {
    loading.value = false
  }
})

function formatDate(iso: string) {
  return new Date(iso).toLocaleString()
}

function formatDuration(s: number) {
  if (!s) return '—'
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`
}

function formatSize(bytes: number) {
  if (!bytes) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
</script>

<style scoped>
.empty-state {
  text-align: center;
  color: var(--text2);
  padding: 40px;
}
.badge-muted {
  color: var(--text2);
  font-size: 12px;
}
</style>
