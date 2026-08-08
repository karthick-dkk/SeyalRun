<template>
  <AppShell>
    <div class="jobs-page">
      <div class="jobs-header">
        <div class="filter-row">
          <select v-model="statusFilter" class="prv-select" @change="load">
            <option value="">All statuses</option>
            <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
          </select>
        </div>
        <span class="text-muted">{{ total }} total jobs</span>
      </div>

      <div class="jobs-table">
        <div class="table-header">
          <span>Status</span>
          <span>Playbook</span>
          <span>Triggered by</span>
          <span>Duration</span>
          <span>Created</span>
          <span></span>
        </div>
        <div v-for="j in jobs" :key="j.id" class="table-row">
          <span :class="`badge badge-${statusBadge(j.status)}`">{{ j.status }}</span>
          <span class="text-mono">{{ j.playbook_id?.slice(0, 8) ?? '—' }}</span>
          <span>{{ j.triggered_by }}</span>
          <span class="text-mono">{{ j.duration_seconds ? `${j.duration_seconds.toFixed(1)}s` : '—' }}</span>
          <span class="text-muted">{{ formatTime(j.created_at) }}</span>
          <router-link :to="`/jobs/${j.id}`" class="view-link">View →</router-link>
        </div>
        <div v-if="jobs.length === 0" class="empty-row">No jobs found.</div>
      </div>

      <div class="pagination" v-if="total > limit">
        <button :disabled="offset === 0" @click="offset -= limit; load()">‹</button>
        <span>{{ Math.floor(offset / limit) + 1 }} / {{ Math.ceil(total / limit) }}</span>
        <button :disabled="offset + limit >= total" @click="offset += limit; load()">›</button>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import AppShell from '@/components/layout/AppShell.vue'
import { jobsApi } from '@/api'
import type { Job } from '@/types'

const jobs = ref<Job[]>([])
const total = ref(0)
const statusFilter = ref('')
const statuses = ['pending', 'running', 'success', 'failed', 'cancelled']
const limit = 20
const offset = ref(0)

function statusBadge(s: string) {
  return { success: 'success', failed: 'error', running: 'info', pending: 'neutral', cancelled: 'neutral' }[s] ?? 'neutral'
}

function formatTime(iso: string) {
  return new Date(iso).toLocaleString()
}

async function load() {
  const resp = await jobsApi.list({ status: statusFilter.value || undefined, limit, offset: offset.value })
  jobs.value = resp.data.items
  total.value = resp.data.total
}

onMounted(load)
</script>

<style scoped>
.jobs-page { display: flex; flex-direction: column; gap: var(--space-5); }
.jobs-header { display: flex; justify-content: space-between; align-items: center; }
.filter-row { display: flex; gap: var(--space-3); }
.prv-select { background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-md); color: var(--text); font-size: 13px; padding: var(--space-2) var(--space-3); outline: none; }

.jobs-table { background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-lg); overflow: hidden; }
.table-header {
  display: grid;
  grid-template-columns: 90px 1fr 1fr 80px 1fr 60px;
  padding: var(--space-3) var(--space-4);
  background: var(--bg-overlay);
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.table-row {
  display: grid;
  grid-template-columns: 90px 1fr 1fr 80px 1fr 60px;
  padding: var(--space-3) var(--space-4);
  align-items: center;
  border-top: 1px solid var(--border);
  font-size: 13px;
  transition: background var(--transition);
}
.table-row:hover { background: var(--bg-overlay); }
.text-mono { font-family: var(--font-mono); font-size: 12px; }
.view-link { font-size: 12px; color: var(--accent); }
.empty-row { padding: var(--space-8); text-align: center; color: var(--text-muted); font-size: 13px; }

.pagination { display: flex; justify-content: center; align-items: center; gap: var(--space-4); }
.pagination button { background: var(--bg-surface); border: 1px solid var(--border); color: var(--text-muted); padding: var(--space-2) var(--space-4); border-radius: var(--radius-md); cursor: pointer; }
.pagination button:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
