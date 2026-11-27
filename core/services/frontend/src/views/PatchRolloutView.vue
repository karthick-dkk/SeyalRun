<template>
  <AppShell>
    <div class="page">
      <div class="ph">
        <div>
          <h1 class="h1">{{ r?.name || 'Rollout' }}</h1>
          <p class="sub"><router-link class="lk" to="/patching">Patching</router-link> / Rollout</p>
        </div>
        <div v-if="r" class="acts">
          <button class="btn ib" type="button" aria-label="Refresh" title="Refresh" @click="load(false)">↺</button>
          <button class="btn" type="button" :disabled="exporting" @click="evidence('json')">Evidence JSON</button>
          <button class="btn" type="button" :disabled="exporting" @click="evidence('csv')">Evidence CSV</button>
          <template v-if="auth.isAdmin">
            <button v-if="r.can_approve" class="btn p" type="button" :disabled="busy" @click="approve">Approve</button>
            <button v-for="a in rolloutActions" :key="a.key" class="btn" :class="a.danger ? 'd' : ''" type="button" :disabled="busy" @click="askReason('rollout', a.key, a.label)">{{ a.label }}</button>
          </template>
        </div>
      </div>

      <div v-if="loading && !r" class="card"><div class="empty-state">Loading…</div></div>
      <div v-else-if="!r" class="card"><div class="empty-state">
        <b>{{ down ? 'Posture service is not available' : 'Rollout not found' }}</b>
        {{ error || 'It may have been removed, or you may not be able to see any of its hosts.' }}
        <div style="margin-top:12px"><button class="btn" type="button" @click="load(true)">Retry</button> <router-link class="btn" to="/patching">Back to Patching</router-link></div>
      </div></div>

      <div v-else class="stack">
        <div v-if="error" class="callout" style="--c:var(--bad)" role="alert">{{ error }}</div>
        <div v-if="notice" class="callout" style="--c:var(--ok)" role="status">{{ notice }}</div>
        <div v-if="r.can_approve" class="callout" style="--c:var(--warn)" role="status">This rollout is waiting for approval. Review the plan below, then approve it.</div>
        <div v-if="r.status === 'pending_approval' && r.approve_blocked_reason" class="callout" style="--c:var(--warn)" role="status">You cannot approve this rollout: {{ r.approve_blocked_reason }}.</div>

        <section class="card" style="padding:16px">
          <dl class="kv">
            <dt>Status</dt><dd><span class="b" :class="rolloutKind(r.status)">{{ label(r.status) }}</span><span v-if="r.waiting" class="muted" style="margin-left:8px">Waiting: {{ r.waiting }}</span></dd>
            <dt>Reason</dt><dd>{{ r.status_reason || '—' }}</dd>
            <dt>Hosts</dt><dd>{{ r.hosts }} hosts, {{ r.packages }} packages</dd>
            <dt>Wave</dt><dd>{{ Math.min(r.current_wave + 1, r.waves) }} of {{ r.waves }}</dd>
            <dt>Counts</dt><dd><span v-for="(n, k) in r.counts" :key="k" class="tag" style="margin-right:4px">{{ label(String(k)) }} {{ n }}</span><span v-if="!Object.keys(r.counts || {}).length" class="muted">—</span></dd>
            <dt>Plan hash</dt><dd class="mono" :title="r.plan_hash">{{ r.plan_hash.slice(0, 12) }}</dd>
            <dt>Feed version</dt><dd class="mono">{{ r.feed_version || '—' }}</dd>
            <dt>Window</dt><dd>{{ windowText }}</dd>
            <dt>Requested by</dt><dd>{{ r.created_by_name || '—' }} <span class="muted">{{ when(r.created_at) }}</span></dd>
            <dt>Approved by</dt><dd>{{ r.approved_by_name || '—' }} <span v-if="r.approved_at" class="muted">{{ when(r.approved_at) }}</span><span v-if="r.approval && r.approval.startsWith('not required')" class="muted"> · second-admin approval {{ r.approval }}</span></dd>
            <dt>Started</dt><dd>{{ when(r.started_at) }}</dd>
            <dt>Finished</dt><dd>{{ when(r.finished_at) }}</dd>
            <dt v-if="r.soak_until">Soak until</dt><dd v-if="r.soak_until">{{ when(r.soak_until) }}</dd>
            <dt>Settings</dt><dd><span v-for="(v, k) in shownConfig" :key="k" class="tag" style="margin:0 4px 4px 0">{{ k }}: {{ Array.isArray(v) ? (v.length ? v.join(', ') : 'none') : v }}</span></dd>
            <dt v-if="r.drain_hook">Drain hook</dt><dd v-if="r.drain_hook">{{ r.drain_hook }} / {{ r.undrain_hook }}</dd>
          </dl>
        </section>

        <section v-for="w in waves" :key="w.n" class="card">
          <div class="card-header">Wave {{ w.n + 1 }} of {{ r.waves }} <span class="cnt" style="margin-left:8px">{{ w.rows.length }}</span></div>
          <div class="tw"><table class="t">
            <thead><tr><th></th><th>Host</th><th>Status</th><th>Step</th><th class="r">Packages</th><th class="r">Changes</th><th>Flags</th><th class="r"><span class="vh">Actions</span></th></tr></thead>
            <tbody>
              <template v-for="h in w.rows" :key="h.host_id">
                <tr>
                  <td><button class="btn sm ib g" type="button" :aria-label="`${open[h.host_id] ? 'Hide' : 'Show'} details for ${h.hostname}`" :aria-expanded="!!open[h.host_id]" @click="open[h.host_id] = !open[h.host_id]">{{ open[h.host_id] ? '▾' : '▸' }}</button></td>
                  <td><span class="nm">{{ h.hostname }}</span><span v-if="h.production" class="tag" style="margin-left:6px">production</span><span v-if="h.error" class="s2" style="color:var(--bad);white-space:normal;max-width:360px">{{ h.error }}</span></td>
                  <td><span class="b" :class="hostKind(h.status)">{{ label(h.status) }}</span><span v-if="h.attempt > 1" class="muted" style="margin-left:6px">attempt {{ h.attempt }}</span></td>
                  <td>{{ h.step ? label(h.step) : '—' }}</td>
                  <td class="r num">{{ h.packages.length }}</td>
                  <td class="r num">{{ h.changes.length }}</td>
                  <td><span v-if="h.drained" class="b warn">Drained</span> <span v-if="h.in_maintenance" class="b info">In maintenance</span><span v-if="!h.drained && !h.in_maintenance" class="muted">—</span></td>
                  <td class="r" style="white-space:normal"><template v-if="auth.isAdmin"><button v-for="a in hostActionsFor(h)" :key="a.key" class="btn sm" style="margin:2px 0 2px 4px" type="button" :disabled="busy" :title="a.hint" @click="askReason('host', a.key, a.label, h)">{{ a.label }}</button></template></td>
                </tr>
                <tr v-if="open[h.host_id]">
                  <td></td>
                  <td colspan="7" style="white-space:normal">
                    <div class="stack" style="gap:12px">
                      <div v-if="h.changes.length">
                        <h3 class="ct" style="margin-bottom:6px">Changes</h3>
                        <ul class="plain"><li v-for="c in h.changes" :key="c.name" class="mono">{{ c.name }}: {{ c.old || '(none)' }} → {{ c.new || '(none)' }}</li></ul>
                      </div>
                      <div v-if="h.packages.length">
                        <h3 class="ct" style="margin-bottom:6px">Planned packages</h3>
                        <ul class="plain"><li v-for="p in h.packages" :key="p.name + p.arch" class="mono">{{ p.name }}<span v-if="p.arch">.{{ p.arch }}</span> {{ p.current }} → {{ p.target }}<span v-if="p.security" class="b bad" style="margin-left:6px">Security</span></li></ul>
                      </div>
                      <div v-if="h.held && h.held.length"><h3 class="ct" style="margin-bottom:6px">Held back</h3><span v-for="x in h.held" :key="String(x)" class="tag mono" style="margin-right:4px">{{ typeof x === 'string' ? x : (x.name || JSON.stringify(x)) }}</span></div>
                      <div v-if="h.reboot"><h3 class="ct" style="margin-bottom:6px">Reboot</h3><span class="mono">{{ typeof h.reboot === 'string' ? h.reboot : JSON.stringify(h.reboot) }}</span></div>
                      <div v-for="g in checkGroups(h)" :key="g.phase">
                        <h3 class="ct" style="margin-bottom:6px">{{ g.phase === 'pre' ? 'Pre-checks' : g.phase === 'post' ? 'Post-checks' : label(g.phase) }}</h3>
                        <ul class="plain"><li v-for="(c, i) in g.items" :key="i"><span class="b" :class="c.ok === true ? 'ok' : c.ok === false ? 'bad' : 'off'">{{ c.ok === true ? 'Pass' : c.ok === false ? 'Fail' : 'Not evaluated' }}</span> <b>{{ c.name }}</b><span v-if="c.detail" class="muted"> {{ c.detail }}</span></li></ul>
                      </div>
                      <div v-if="h.findings.length">
                        <h3 class="ct" style="margin-bottom:6px">Findings addressed</h3>
                        <span v-for="f in h.findings" :key="f.id" class="tag mono" style="margin:0 4px 4px 0">{{ f.cve }} {{ f.package }}</span>
                      </div>
                      <div v-if="h.repos.length"><span class="muted">Mirrors: </span><span v-for="s in h.repos" :key="s" class="tag" style="margin-right:4px">{{ s }}</span></div>
                      <div><span class="muted">Job runs: </span><template v-for="(rid, k) in h.runs" :key="k"><router-link v-if="rid" class="lk" style="margin-right:8px" :to="`/jobs/${rid}`">{{ label(String(k)) }}</router-link></template><span v-if="!Object.keys(h.runs || {}).length" class="muted">—</span></div>
                      <div class="muted">Started {{ when(h.started_at) }}, finished {{ when(h.finished_at) }}</div>
                    </div>
                  </td>
                </tr>
              </template>
            </tbody>
          </table></div>
        </section>
        <div v-if="!waves.length" class="card"><div class="empty-state"><b>No hosts</b>You may not be able to see any host in this rollout.</div></div>

        <section class="card">
          <div class="card-header">Events <span class="cnt" style="margin-left:8px">{{ r.events.length }}</span></div>
          <div class="tw" style="max-height:340px;overflow:auto"><table class="t">
            <thead><tr><th>When</th><th>Event</th><th>Host</th><th>By</th><th>Detail</th></tr></thead>
            <tbody><tr v-for="(e, i) in r.events" :key="i">
              <td class="muted">{{ when(e.at) }}</td><td>{{ label(e.event) }}</td><td>{{ e.host_id ? hostName(e.host_id) : '—' }}</td><td>{{ e.by || '—' }}</td>
              <td style="white-space:normal;min-width:240px">{{ e.detail || '—' }}</td></tr></tbody>
          </table></div>
          <div v-if="!r.events.length" class="empty-state">No events yet.</div>
        </section>
      </div>
    </div>

    <!-- Reason dialog -->
    <div v-if="dlg" class="modal-overlay" @click.self="dlg = null">
      <div class="modal" role="dialog" :aria-label="dlg.label" style="width:min(480px,96vw)">
        <div class="modal-header"><span>{{ dlg.label }}<span v-if="dlg.host" class="muted" style="font-weight:400;margin-left:8px">{{ dlg.host.hostname }}</span></span><button class="btn sm ib g" type="button" aria-label="Close" @click="dlg = null">×</button></div>
        <div class="modal-body stack" style="gap:12px">
          <p v-if="dlg.hint" class="muted" style="margin:0">{{ dlg.hint }}</p>
          <label for="pr-reason" class="ct">Reason (at least 3 characters)</label>
          <textarea id="pr-reason" v-model="reason" class="in" rows="3" maxlength="1000" style="height:auto;padding:8px 10px"></textarea>
          <div v-if="dlgError" class="callout" style="--c:var(--bad)" role="alert">{{ dlgError }}</div>
        </div>
        <div class="modal-footer"><button class="btn" type="button" @click="dlg = null">Cancel</button><button class="btn p" type="button" :disabled="reason.trim().length < 3 || busy" @click="submitReason">{{ busy ? 'Working…' : dlg.label }}</button></div>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useConfirm } from '@/composables/useConfirm'
import { detailOf, isDown, when, label, rolloutKind, hostKind, downloadBlob, DAYS } from '@/composables/patchingUtil'

const auth = useAuthStore()
const route = useRoute()
const { confirm } = useConfirm()
let id = String(route.params.id)
const r = ref<any>(null)
const loading = ref(true)
const down = ref(false)
const error = ref('')
const notice = ref('')
const busy = ref(false)
const exporting = ref(false)
const open = reactive<Record<string, boolean>>({})

async function load(showSpinner = true) {
  if (showSpinner) loading.value = true
  const asked = id
  try {
    const d = (await api.get(`/posture/patches/${id}`)).data
    if (asked !== id) return
    r.value = d; error.value = ''; down.value = false }
  catch (e: any) {
    if (!r.value) { down.value = isDown(e) && e?.response?.status !== 404; error.value = detailOf(e, 'Could not load the rollout.') }
    else error.value = detailOf(e, 'Could not refresh the rollout.')
  } finally { loading.value = false }
}

// Poll every 5 s only while the engine is moving; stop on unmount.
let timer: ReturnType<typeof setInterval> | null = null
function stopPoll() { if (timer) { clearInterval(timer); timer = null } }
// A halted rollout can still be releasing or rolling back a host.
watch(() => r.value?.status === 'approved' || r.value?.status === 'running' || !!r.value?.counts?.running, (moving) => {
  stopPoll()
  if (moving) timer = setInterval(() => load(false), 5000)
})
onMounted(() => load())
// The router reuses this component when only the id changes.
watch(() => route.params.id, (next) => {
  if (!next || String(next) === id) return
  id = String(next); r.value = null; error.value = ''; notice.value = ''
  for (const k of Object.keys(open)) delete open[k]
  load()
})
onBeforeUnmount(stopPoll)

const windowText = computed(() => {
  const w = r.value?.window
  if (!w) return 'None (runs as soon as approved)'
  return `${w.name}: ${(w.days || []).map((d: number) => DAYS[d]?.slice(0, 3)).join(', ')} ${w.start} ${w.timezone}, ${w.duration_minutes} min`
})
const shownConfig = computed(() => r.value?.config || {})
const waves = computed(() => {
  const m = new Map<number, any[]>()
  for (const h of r.value?.host_rows || []) { if (!m.has(h.wave)) m.set(h.wave, []); m.get(h.wave)!.push(h) }
  return [...m.entries()].sort((a, b) => a[0] - b[0]).map(([n, rows]) => ({ n, rows }))
})
const hostName = (hid: string) => (r.value?.host_rows || []).find((h: any) => h.host_id === hid)?.hostname || hid

// Which actions the API allows (see _decide / HOST_ACTIONS in patching.py).
const ROLLOUT_ACTIONS = [
  { key: 'reject', label: 'Reject', from: ['pending_approval'], danger: true },
  { key: 'pause', label: 'Pause', from: ['approved', 'running'] },
  { key: 'resume', label: 'Resume', from: ['paused', 'halted'] },
  { key: 'cancel', label: 'Cancel rollout', from: ['pending_approval', 'approved', 'running', 'paused', 'halted'], danger: true },
]
const rolloutActions = computed(() => ROLLOUT_ACTIONS.filter((a) => a.from.includes(r.value?.status)))
const HOST_ACTIONS = [
  { key: 'retry', label: 'Retry', from: ['failed'], hint: 'Run this host again from its pre-checks.' },
  { key: 'skip', label: 'Skip', from: ['pending', 'failed', 'rolled_back'], hint: 'Leave this host out.' },
  { key: 'rollback', label: 'Roll back', from: ['failed', 'done'], hint: 'Put the changed packages back.' },
  { key: 'release', label: 'Release', from: ['failed', 'rolled_back', 'skipped', 'cancelled'], hint: 'Undrain and end maintenance.' },
  { key: 'force_release', label: 'Force release', from: ['failed', 'rolled_back', 'skipped', 'cancelled'], hint: 'Only when Release cannot work: record that a person undrained this host and ended its maintenance by hand. Runs nothing.' },
]
function hostActionsFor(h: any) {
  const st = r.value?.status
  return HOST_ACTIONS.filter((a) => {
    if (!a.from.includes(h.status)) return false
    if ((a.key === 'retry' || a.key === 'rollback') && !['running', 'paused', 'halted'].includes(st)) return false
    if (a.key === 'rollback' && !h.changes.some((c: any) => c.old && c.new)) return false
    if (a.key === 'release' && !r.value?.started_at) return false
    if (a.key === 'force_release' && !h.drained && !h.maintenance_id) return false
    return true
  })
}
function checkGroups(h: any) {
  const m = new Map<string, any[]>()
  for (const c of h.checks || []) { const p = c.phase || 'other'; if (!m.has(p)) m.set(p, []); m.get(p)!.push(c) }
  return [...m.entries()].map(([phase, items]) => ({ phase, items }))
}

async function approve() {
  if (!r.value) return
  const ok = await confirm(
    `Approve "${r.value.name}"?\n\nThis lets the rollout change ${r.value.hosts} host${r.value.hosts === 1 ? '' : 's'} and install ${r.value.packages} package update${r.value.packages === 1 ? '' : 's'}.\nPlan hash: ${r.value.plan_hash.slice(0, 12)}\n\nApproval applies only to this exact plan.`,
    { title: 'Approve rollout', confirmLabel: 'Approve' })
  if (!ok) return
  busy.value = true; error.value = ''; notice.value = ''
  try { await api.post(`/posture/patches/${id}/approve`, { plan_hash: r.value.plan_hash }); notice.value = 'Rollout approved.'; await load(false) }
  catch (e: any) { error.value = detailOf(e, 'Approval failed.'); await load(false) } finally { busy.value = false }
}

const dlg = ref<any>(null)
const reason = ref('')
const dlgError = ref('')
function askReason(kind: 'rollout' | 'host', key: string, lbl: string, host?: any) {
  const hint = kind === 'host' ? HOST_ACTIONS.find((a) => a.key === key)?.hint : ''
  dlg.value = { kind, key, label: lbl, host, hint }; reason.value = ''; dlgError.value = ''
}
async function submitReason() {
  const d = dlg.value
  if (!d || reason.value.trim().length < 3) return
  busy.value = true; dlgError.value = ''
  try {
    const url = d.kind === 'host' ? `/posture/patches/${id}/hosts/${d.host.host_id}/${d.key}` : `/posture/patches/${id}/${d.key}`
    await api.post(url, { reason: reason.value.trim() })
    dlg.value = null; notice.value = `${d.label} done.`; error.value = ''
    await load(false)
  } catch (e: any) { dlgError.value = detailOf(e, `${d.label} failed.`) } finally { busy.value = false }
}

async function evidence(format: 'json' | 'csv') {
  exporting.value = true; error.value = ''
  try { await downloadBlob(`/posture/patches/${id}/evidence`, { format }, `patch-evidence-${id.slice(0, 8)}.${format}`) }
  catch (e: any) { error.value = 'Could not download the evidence file.' } finally { exporting.value = false }
}
</script>

<style scoped>
.plain { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 4px; }
</style>
