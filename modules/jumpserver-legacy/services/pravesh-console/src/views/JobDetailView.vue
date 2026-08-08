<template>
  <AppShell>
    <div v-if="job" class="job-detail">
      <div class="job-header">
        <router-link to="/jobs" class="back-link">← Jobs</router-link>
        <div class="job-title-row">
          <span class="job-id text-mono">{{ job.id }}</span>
          <span :class="`badge badge-${statusBadge(job.status)}`">{{ job.status }}</span>
        </div>
        <div class="job-meta">
          <span>by {{ job.triggered_by }}</span>
          <span v-if="job.duration_seconds">{{ job.duration_seconds.toFixed(1) }}s</span>
          <span>exit {{ job.exit_code ?? '—' }}</span>
          <span class="text-muted">{{ formatTime(job.created_at) }}</span>
        </div>
      </div>

      <TerminalOutput
        :lines="streamLines.length ? streamLines : job.output_lines"
        :done="streamDone || job.status !== 'running'"
        :exit-code="job.exit_code"
        :title="`Job ${job.id.slice(0, 8)}`"
      />

      <div class="job-actions" v-if="job.status === 'running'">
        <button class="btn-danger" @click="cancelJob">Cancel Job</button>
      </div>
    </div>
    <div v-else class="loading">Loading…</div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'
import TerminalOutput from '@/components/terminal/TerminalOutput.vue'
import { jobsApi } from '@/api'
import { useJobStream } from '@/composables/useJobStream'
import { useUiStore } from '@/stores/ui'
import type { Job } from '@/types'

const route = useRoute()
const ui = useUiStore()
const job = ref<Job | null>(null)
const jobId = route.params.id as string

const { lines: streamLines, done: streamDone, connect } = useJobStream(jobId)

function statusBadge(s: string) {
  return { success: 'success', failed: 'error', running: 'info', pending: 'neutral', cancelled: 'neutral' }[s] ?? 'neutral'
}

function formatTime(iso: string) { return new Date(iso).toLocaleString() }

async function cancelJob() {
  await jobsApi.cancel(jobId)
  ui.success('Cancel requested')
  job.value = (await jobsApi.get(jobId)).data
}

onMounted(async () => {
  job.value = (await jobsApi.get(jobId)).data
  if (job.value.status === 'running') {
    connect()
  }
})
</script>

<style scoped>
.job-detail { display: flex; flex-direction: column; gap: var(--space-5); }
.back-link { font-size: 13px; color: var(--text-muted); }
.job-title-row { display: flex; align-items: center; gap: var(--space-3); }
.job-id { font-size: 14px; font-weight: 600; }
.job-meta { display: flex; gap: var(--space-4); font-size: 12px; font-family: var(--font-mono); color: var(--text-muted); }
.job-actions { display: flex; justify-content: flex-end; }
.btn-danger { background: var(--error-dim); color: var(--error); border: 1px solid var(--error); border-radius: var(--radius-md); padding: var(--space-2) var(--space-4); font-size: 13px; cursor: pointer; }
.loading { text-align: center; color: var(--text-muted); padding: var(--space-10); }
</style>
