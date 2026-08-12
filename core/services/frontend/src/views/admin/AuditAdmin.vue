<template>
  <div>
    <!-- Chain verification. The tamper-evident hash chain is the control this
         product leads with, and until now it had no presence in the interface
         at all: /audit/verify existed but nothing in the SPA ever called it, so
         the only way to answer "is this log intact?" was curl. An assessor
         looking at this page could read the entries and learn nothing about
         whether they had been altered. -->
    <div class="card audit-verify" :class="verifyClass">
      <div class="audit-verify-body">
        <span class="audit-verify-icon" v-html="verifyIcon" aria-hidden="true" />
        <div>
          <div class="audit-verify-headline">{{ verifyHeadline }}</div>
          <div class="audit-verify-detail">{{ verifyDetail }}</div>
        </div>
      </div>
      <button class="btn btn-sm" @click="runVerify" :disabled="verifying">
        {{ verifying ? 'Verifying…' : 'Re-verify' }}
      </button>
    </div>

    <div class="card">
      <div class="card-header">Audit Logs</div>
      <table class="table">
        <thead>
          <tr><th>Time</th><th>User</th><th>Action</th><th>Result</th><th>Resource</th><th>Details</th><th>IP Address</th></tr>
        </thead>
        <tbody>
          <tr v-for="l in logs" :key="l.id">
            <td style="font-size:12px;color:var(--text2);white-space:nowrap">{{ formatDate(l.created_at) }}</td>
            <td style="font-weight:600">{{ l.username }}</td>
            <td><span class="badge badge-blue">{{ l.action }}</span></td>
            <!-- PCI DSS Req 10.2.2 counts a success/failure indication as a
                 required element of an audit record. It was being stored on
                 some rows and displayed on none. Rows written before that was
                 recorded show "—" rather than guessing an outcome. -->
            <td>
              <span v-if="l.result" class="badge" :class="resultClass(l.result)">{{ l.result }}</span>
              <span v-else style="color:var(--text2)">—</span>
            </td>
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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
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
function resultClass(r: string) {
  return /fail|denied|error/i.test(r) ? 'badge-red' : 'badge-green'
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

// ── Chain verification ──────────────────────────────────────────────────────
const verify = ref<any>(null)
const verifying = ref(false)
const verifyFailed = ref(false)

async function runVerify() {
  verifying.value = true
  verifyFailed.value = false
  try {
    const { data } = await api.get('/audit/verify')
    verify.value = data
  } catch {
    // A failed request is NOT a broken chain, and must never be shown as one —
    // "cannot check" and "was tampered with" are different answers.
    verifyFailed.value = true
    verify.value = null
  } finally {
    verifying.value = false
  }
}

const verifyState = computed(() => {
  if (verifying.value && !verify.value) return 'checking'
  if (verifyFailed.value) return 'unknown'
  if (!verify.value) return 'checking'
  return verify.value.ok ? 'ok' : 'broken'
})

const verifyClass = computed(() => `audit-verify--${verifyState.value}`)

const verifyHeadline = computed(() => ({
  checking: 'Checking the audit chain…',
  unknown: 'Could not check the audit chain',
  ok: 'Audit chain intact',
  broken: 'Audit chain does NOT verify',
}[verifyState.value]))

const verifyDetail = computed(() => {
  const v = verify.value
  switch (verifyState.value) {
    case 'ok':
      return `${v.checked} entr${v.checked === 1 ? 'y' : 'ies'} verified from genesis — every row still hashes to the value stored when it was written.`
    case 'broken':
      return `Breaks at sequence ${v.broken_seq}: ${v.reason}. Entries at or after that point cannot be relied on as evidence.`
    case 'unknown':
      return 'The verification endpoint could not be reached. This says nothing about whether the log is intact.'
    default:
      return 'Recomputing every entry hash against the stored chain.'
  }
})

const ICON = {
  ok: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2.25 4.5 5.063v6.562a8.25 8.25 0 007.5 8.25 8.25 8.25 0 007.5-8.25V5.063L12 2.25z"/><path d="m9 12.75 2.25 2.25 3.75-5.25"/></svg>',
  broken: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v3.75m0 3.75h.008M10.34 3.94l-8.02 13.9A1.5 1.5 0 003.62 20.1h16.76a1.5 1.5 0 001.3-2.26l-8.02-13.9a1.5 1.5 0 00-2.6 0z"/></svg>',
  unknown: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9.9 9.3a2.25 2.25 0 114.05 1.35c-.6.75-1.6 1.05-1.85 1.95M12 16.5h.008"/></svg>',
  checking: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M16.02 9.35h5M2.99 19.64v-5m0 0h5m-5 0 3.18 3.18a8.25 8.25 0 0013.8-3.7M4.03 9.87a8.25 8.25 0 0113.8-3.7L21 9.35"/></svg>',
}
const verifyIcon = computed(() => ICON[verifyState.value as keyof typeof ICON])

onMounted(() => { loadMore(); runVerify() })
</script>

<style scoped>
.audit-verify {
  display: flex; align-items: center; justify-content: space-between; gap: 16px;
  padding: 14px 18px; margin-bottom: 16px;
}
.audit-verify-body { display: flex; align-items: center; gap: 12px; }
.audit-verify-icon { display: flex; width: 26px; height: 26px; flex-shrink: 0; }
.audit-verify-icon :deep(svg) { width: 100%; height: 100%; }
.audit-verify-headline { font-weight: 600; font-size: 14px; }
.audit-verify-detail { font-size: 12.5px; color: var(--text2); margin-top: 2px; }
.audit-verify--ok      { border-color: rgba(34,197,94,0.4); }
.audit-verify--ok      .audit-verify-icon, .audit-verify--ok .audit-verify-headline { color: var(--accent); }
.audit-verify--broken  { border-color: rgba(239,68,68,0.5); background: rgba(239,68,68,0.06); }
.audit-verify--broken  .audit-verify-icon, .audit-verify--broken .audit-verify-headline { color: var(--danger); }
.audit-verify--unknown .audit-verify-icon, .audit-verify--unknown .audit-verify-headline { color: var(--warn); }
.audit-verify--checking .audit-verify-icon { color: var(--text2); }
</style>
