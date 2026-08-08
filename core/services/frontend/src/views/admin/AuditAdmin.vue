<template>
  <div class="card">
    <div class="card-header">
      Audit Logs
    </div>
    <table class="table">
      <thead>
        <tr><th>Time</th><th>User</th><th>Action</th><th>Resource</th><th>Details</th><th>IP Address</th></tr>
      </thead>
      <tbody>
        <tr v-for="l in logs" :key="l.id">
          <td style="font-size:12px;color:var(--text2);white-space:nowrap">{{ formatDate(l.created_at) }}</td>
          <td style="font-weight:600">{{ l.username }}</td>
          <td><span class="badge badge-blue">{{ l.action }}</span></td>
          <td style="font-size:12px">
            <span class="badge badge-gray">{{ l.resource_type }}</span>
            <span style="color:var(--text2)">{{ l.resource_id }}</span>
          </td>
          <td style="font-size:12px;color:var(--text2);max-width:360px;overflow:hidden;text-overflow:ellipsis">{{ formatDetails(l.details) }}</td>
          <td><span class="ip-mono">{{ l.ip_address }}</span></td>
        </tr>
      </tbody>
    </table>
    <div v-if="!logs.length && !loading" style="padding:32px;text-align:center;color:var(--text2)">No audit log entries yet.</div>
    <div v-if="loading" style="padding:24px;text-align:center;color:var(--text2)">Loading…</div>
    <div style="display:flex;justify-content:center;padding:16px;border-top:1px solid var(--border)">
      <button class="btn" @click="loadMore" :disabled="loading || !hasMore">{{ hasMore ? 'Load more' : 'No more entries' }}</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api/client'

const PAGE_SIZE = 100

const logs = ref<any[]>([])
const loading = ref(false)
const offset = ref(0)
const hasMore = ref(true)

function formatDate(d: string) { return new Date(d).toLocaleString() }
function formatDetails(details: Record<string, unknown>) {
  if (!details || !Object.keys(details).length) return '—'
  return JSON.stringify(details)
}

async function loadMore() {
  loading.value = true
  try {
    const { data } = await api.get('/audit/logs', { params: { limit: PAGE_SIZE, offset: offset.value } })
    logs.value.push(...data)
    offset.value += data.length
    hasMore.value = data.length === PAGE_SIZE
  } finally {
    loading.value = false
  }
}

onMounted(loadMore)
</script>
