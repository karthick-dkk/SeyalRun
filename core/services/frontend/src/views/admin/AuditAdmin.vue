<template>
  <div class="stack">
    <!-- Chain verification: "cannot check" and "tampered with" are different answers, shown differently. -->
    <section class="card audit-verify" :class="verifyClass" :aria-live="verifyState === 'broken' ? 'assertive' : 'polite'">
      <div class="audit-verify-body">
        <span class="audit-verify-icon" v-html="verifyIcon" aria-hidden="true" />
        <div>
          <div class="audit-verify-headline">{{ verifyHeadline }}</div>
          <div class="audit-verify-detail">{{ verifyDetail }}</div>
        </div>
      </div>
      <button class="btn sm" type="button" @click="runVerify" :disabled="verifying">
        {{ verifying ? 'Verifying…' : 'Re-verify' }}
      </button>
    </section>

    <section class="card">
      <div class="ch"><h2 class="ct">Audit logs</h2>
        <span class="seg" role="group" aria-label="Filter by action">
          <button v-for="f in FILTERS" :key="f.key" type="button" :class="{ on: actionFilter === f.key }" @click="actionFilter = f.key; applyFilter()">{{ f.label }}</button>
        </span>
        <button type="button" class="btn sm" :class="{ p: resultFilter === 'failure' }" :aria-pressed="resultFilter === 'failure'"
          @click="resultFilter = resultFilter === 'failure' ? '' : 'failure'; applyFilter()"
          title="Show only refused or failed operations">Failures only</button>
      </div>
      <div class="tw">
        <table class="t">
          <thead>
            <tr><th>Time</th><th>User</th><th>Action</th><th>Result</th><th>Resource</th><th>Details</th><th>IP address</th></tr>
          </thead>
          <tbody>
            <tr v-for="l in logs" :key="l.id">
              <td class="muted">{{ formatDate(l.created_at) }}</td>
              <td><span class="nm">{{ l.username }}</span></td>
              <td><span class="b nd info">{{ l.action }}</span></td>
              <!-- PCI DSS 10.2.2: success/failure indication. Rows written before it was recorded show a dash. -->
              <td>
                <span v-if="l.result" class="b" :class="resultClass(l.result)">{{ l.result }}</span>
                <span class="muted" v-else>—</span>
              </td>
              <td>
                <span class="tag">{{ l.resource_type }}</span>
                <span class="muted mono" style="margin-left:6px">{{ l.resource_id }}</span>
              </td>
              <td class="muted audit-details" :title="formatDetails(l.details)">{{ formatDetails(l.details) }}</td>
              <td class="mono">{{ l.ip_address }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="empty-state" v-if="!logs.length && !loading"><b>No audit log entries</b>Entries appear here as users act.</div>
      <div class="empty-state" v-if="loading">Loading…</div>
      <div class="foot" style="justify-content:center">
        <button class="btn" type="button" @click="loadMore" :disabled="loading || !hasMore">{{ hasMore ? 'Load more' : 'No more entries' }}</button>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
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
  return /fail|denied|error/i.test(r) ? 'bad' : 'ok'
}

// Quick filters. Without these, "show me the file transfers" meant paging
// through every row in the log — and "what left this host, and was anything
// refused?" is exactly the question the chain is kept to answer.
const FILTERS = [
  { key: '',              label: 'All' },
  { key: 'sftp.',         label: 'File transfer' },
  { key: 'credential.',   label: 'Credentials' },
  { key: 'session.',      label: 'Sessions' },
  { key: 'auth.',         label: 'Authentication' },
  { key: 'mcp.',          label: 'MCP' },
]
// The MCP page links here with ?action=mcp. — start on that filter.
const route = useRoute()
const _qa = typeof route.query.action === 'string' ? route.query.action : ''
const actionFilter = ref(FILTERS.some(f => f.key === _qa) ? _qa : '')
const resultFilter = ref('')

function applyFilter() {
  logs.value = []
  offset.value = 0
  hasMore.value = true
  loadMore()
}

async function loadMore() {
  loading.value = true
  try {
    const params: Record<string, any> = { limit: PAGE_SIZE, offset: offset.value }
    if (actionFilter.value) params.action = actionFilter.value
    if (resultFilter.value) params.result = resultFilter.value
    const { data } = await api.get('/audit/logs', { params })
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
.audit-verify { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 14px 18px; flex-wrap: wrap; }
.audit-verify-body { display: flex; align-items: center; gap: 12px; }
.audit-verify-icon { display: flex; width: 26px; height: 26px; flex-shrink: 0; }
.audit-verify-icon :deep(svg) { width: 100%; height: 100%; }
.audit-verify-headline { font-weight: 600; font-size: 14px; }
.audit-verify-detail { font-size: 12.5px; color: var(--muted); margin-top: 2px; }
.audit-verify--ok { border-color: color-mix(in srgb, var(--ok) 40%, var(--border)); }
.audit-verify--ok .audit-verify-icon, .audit-verify--ok .audit-verify-headline { color: var(--ok); }
.audit-verify--broken { border-color: color-mix(in srgb, var(--bad) 50%, var(--border)); background: color-mix(in srgb, var(--bad) 7%, var(--surface)); }
.audit-verify--broken .audit-verify-icon, .audit-verify--broken .audit-verify-headline { color: var(--bad); }
.audit-verify--unknown .audit-verify-icon, .audit-verify--unknown .audit-verify-headline { color: var(--warn); }
.audit-verify--checking .audit-verify-icon { color: var(--muted); }
.audit-details { max-width: 360px; overflow: hidden; text-overflow: ellipsis; font-size: 12.5px; }
.foot { display: flex; padding: 12px 16px; border-top: 1px solid var(--border); }
</style>
