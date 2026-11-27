<template>
  <AppShell>
    <div class="page">
      <div class="ph">
        <div>
          <h1 class="h1">Posture</h1>
          <p class="sub">Vulnerabilities, pending updates and reboots across your Linux hosts.</p>
        </div>
        <div class="acts">
          <button class="btn ib" type="button" aria-label="Refresh" title="Refresh" @click="reload">↺</button>
          <router-link class="btn" to="/posture/hosts">Hosts</router-link>
          <button v-if="tab === 'scanners' && auth.isAdmin && !unavailable" class="btn" type="button" @click="openImport()">Import results</button>
          <button v-if="tab === 'scanners' && auth.isAdmin && !unavailable" class="btn p" type="button" :disabled="scanning" @click="runNative">{{ scanning ? 'Queuing…' : 'Run native scan' }}</button>
          <label v-if="tab === 'feeds' && auth.isAdmin && !unavailable" class="btn p" :class="{ 'is-dis': uploading }" style="cursor:pointer">
            {{ uploading ? 'Uploading…' : 'Import feed bundle' }}
            <input type="file" class="vh" aria-label="Feed bundle file" :disabled="uploading" @change="onFile" />
          </label>
        </div>
      </div>

      <div v-if="unavailable" class="card"><div class="empty-state">
        <b>Posture service is not available</b>
        The posture service is not deployed or not reachable yet. Once it is running, host facts and feed bundles appear here.
        <div style="margin-top:12px"><button class="btn" type="button" @click="reload">Retry</button></div>
      </div></div>

      <div v-else class="stack">
        <div v-if="fSummary?.feed_stale" class="callout" style="--c:var(--warn)" role="alert">
          <span>Vulnerability feeds are {{ fSummary.feed_age_days }} days old (limit {{ fSummary.stale_after_days }}). <a class="lk" href="#" @click.prevent="setTab('feeds')">Import a new feed bundle.</a></span>
        </div>
        <section class="tiles" aria-label="Vulnerabilities">
          <button type="button" class="card stat tile" @click="showFindings({})"><span class="l">Open findings</span><span class="v">{{ fSummary?.open_total ?? '—' }}</span><span class="d">{{ fSummary?.hosts_affected ?? '—' }} hosts affected</span></button>
          <button type="button" class="card stat tile" @click="showFindings({ kev: true })"><span class="l">KEV open</span><span class="v" :style="fSummary?.kev_open ? 'color:var(--bad)' : ''">{{ fSummary?.kev_open ?? '—' }}</span><span class="d">Known exploited</span></button>
          <button v-for="sv in SEVS" :key="sv" type="button" class="card stat tile" @click="showFindings({ severity: sv })"><span class="l">{{ cap(sv) }}</span><span class="v">{{ fSummary?.by_severity?.[sv] ?? '—' }}</span><span class="d"><span class="b nd" :class="sevKind(sv)">{{ cap(sv) }}</span></span></button>
        </section>
        <section class="tiles" aria-label="Hosts">
          <router-link class="card stat tile" to="/posture/hosts"><span class="l">Hosts reporting</span><span class="v">{{ summary ? `${summary.hosts_reporting} / ${summary.hosts_total}` : '—' }}</span><span class="d">Facts collected at least once</span></router-link>
          <router-link class="card stat tile" :to="{ path: '/posture/hosts', query: { show: 'security' } }"><span class="l">Security updates</span><span class="v">{{ summary?.security_updates ?? '—' }}</span><span class="d">Pending across all hosts</span></router-link>
          <router-link class="card stat tile" :to="{ path: '/posture/hosts', query: { show: 'reboot' } }"><span class="l">Reboot required</span><span class="v" :style="summary?.reboot_required ? 'color:var(--warn)' : ''">{{ summary?.reboot_required ?? '—' }}</span><span class="d">Hosts waiting on a restart</span></router-link>
          <router-link class="card stat tile" :to="{ path: '/posture/hosts', query: { show: 'attention' } }"><span class="l">Stale or failed</span><span class="v">{{ attention }}</span><span class="d">Facts old, failed or incomplete</span></router-link>
          <button type="button" class="card stat tile" @click="setTab('feeds')"><span class="l">Active feed</span><span class="v" style="font-size:20px;line-height:1.5">{{ summary?.active_feed_version || 'None' }}</span><span class="d">{{ summary?.feed_age_days != null ? `${summary.feed_age_days} day${summary.feed_age_days === 1 ? '' : 's'} old` : 'No bundle activated' }}</span></button>
        </section>

        <div class="tabs" role="tablist">
          <button role="tab" :aria-selected="tab === 'findings'" :class="['tab', { on: tab === 'findings' }]" @click="setTab('findings')">Findings <span class="cnt">{{ fTotal }}</span></button>
          <button role="tab" :aria-selected="tab === 'unmatched'" :class="['tab', { on: tab === 'unmatched' }]" @click="setTab('unmatched')">Unmatched <span class="cnt" :class="{ hot: unmatched.length }">{{ unmatched.length }}</span></button>
          <button role="tab" :aria-selected="tab === 'scanners'" :class="['tab', { on: tab === 'scanners' }]" @click="setTab('scanners')">Scanners <span class="cnt">{{ scanners.length }}</span></button>
          <button role="tab" :aria-selected="tab === 'feeds'" :class="['tab', { on: tab === 'feeds' }]" @click="setTab('feeds')">Feed bundles <span class="cnt">{{ feeds.length }}</span></button>
        </div>
        <div v-if="error" class="callout" style="--c:var(--bad)" role="alert">{{ error }}</div>
        <div v-if="notice" class="callout" style="--c:var(--ok)" role="status">{{ notice }}</div>

        <!-- Findings -->
        <template v-if="tab === 'findings'">
          <section class="card">
            <div class="bar">
              <div class="srch" style="flex:1 1 220px;max-width:340px"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg><label for="f-q" class="vh">Search findings</label><input id="f-q" v-model="fq" class="in" placeholder="CVE, package or host…" @input="onFilter" /></div>
              <label class="vh" for="f-sev">Severity</label>
              <select id="f-sev" v-model="fSeverity" class="in" style="width:auto" @change="onFilter"><option value="">All severities</option><option v-for="sv in SEVS" :key="sv" :value="sv">{{ cap(sv) }}</option></select>
              <label class="vh" for="f-status">Status</label>
              <select id="f-status" v-model="fStatus" class="in" style="width:auto" @change="onFilter"><option value="">Any status</option><option value="open">Open</option><option value="fixed">Fixed</option><option value="accepted">Accepted</option></select>
              <label class="vh" for="f-host">Host</label>
              <select id="f-host" v-model="fHost" class="in" style="width:auto;max-width:200px" @change="onFilter"><option value="">All hosts</option><option v-for="h in hostOptions" :key="h.id" :value="h.id">{{ h.name }}</option></select>
              <label class="row" style="gap:6px;font-size:13px"><input v-model="fKev" type="checkbox" class="chk" @change="onFilter" /> KEV only</label>
            </div>
            <div class="tw"><table class="t">
              <thead><tr><th>CVE</th><th>Severity</th><th>Host</th><th>Package</th><th>Installed</th><th>Fixed in</th><th class="r"><button type="button" class="btn sm g" :aria-label="`Sort by risk, currently ${fSort === 'risk' ? 'highest first' : 'off'}`" @click="toggleSort">Risk {{ fSort === 'risk' ? '▼' : '' }}</button></th><th>Sources</th><th>Status</th></tr></thead>
              <tbody>
                <tr v-for="f in findings" :key="f.id" style="cursor:pointer" tabindex="0" @click="openFinding(f)" @keydown.enter="openFinding(f)">
                  <td><span class="nm mono">{{ f.cve_id }}</span> <span v-if="f.kev" class="b bad" title="Listed in CISA Known Exploited Vulnerabilities">KEV</span></td>
                  <td><span class="b" :class="sevKind(f.severity)">{{ cap(f.severity) }}</span></td>
                  <td @click.stop><router-link class="lk" :to="`/posture/hosts/${f.host_id}`">{{ f.hostname || f.host_id }}</router-link></td>
                  <td class="mono">{{ f.package }}</td>
                  <td class="mono muted">{{ f.installed_version }}</td>
                  <td class="mono">{{ f.fixed_version || '—' }}</td>
                  <td class="r num"><b>{{ f.risk_score ?? '—' }}</b></td>
                  <td><span v-for="s in f.sources || []" :key="s" class="tag" style="margin-right:4px">{{ s }}</span></td>
                  <td><span class="b" :class="fStatusKind(f.status)">{{ cap(f.status) }}</span></td>
                </tr>
              </tbody>
            </table></div>
            <div v-if="fLoading" class="empty-state">Loading…</div>
            <div v-else-if="fDown" class="empty-state"><b>Findings are not available</b>The findings API did not answer. <button class="btn sm" type="button" @click="loadFindings">Retry</button></div>
            <div v-else-if="!findings.length" class="empty-state"><b>No findings</b>{{ fFiltered ? 'Nothing matches these filters.' : 'No vulnerabilities matched yet. Collect host facts, activate a feed bundle or import scanner results.' }}</div>
            <div v-if="fTotal > PAGE" class="foot"><span>{{ fOffset + 1 }}–{{ Math.min(fOffset + PAGE, fTotal) }} of {{ fTotal }}</span><span class="acts"><button class="btn sm" type="button" :disabled="fOffset === 0" @click="page(-1)">Previous</button><button class="btn sm" type="button" :disabled="fOffset + PAGE >= fTotal" @click="page(1)">Next</button></span></div>
          </section>
        </template>

        <!-- Unmatched -->
        <section v-else-if="tab === 'unmatched'" class="card">
          <div class="tw"><table class="t">
            <thead><tr><th>Reported host</th><th>IP</th><th>Scanner</th><th class="r">CVEs</th><th>Received</th><th class="r"><span class="vh">Actions</span></th></tr></thead>
            <tbody>
              <tr v-for="u in unmatched" :key="u.id">
                <td><span class="nm">{{ u.reported_host || '—' }}</span></td>
                <td class="mono">{{ u.reported_ip || '—' }}</td>
                <td><span class="tag">{{ u.scanner }}</span></td>
                <td class="r num">{{ u.cve_count }}</td>
                <td class="muted">{{ u.received_at ? ago(u.received_at) : '—' }}</td>
                <td class="r"><button v-if="auth.isAdmin" class="btn sm" type="button" @click="openAssign(u)">Assign to host</button></td>
              </tr>
            </tbody>
          </table></div>
          <div v-if="uLoading" class="empty-state">Loading…</div>
          <div v-else-if="uDown" class="empty-state"><b>Unmatched results are not available</b>The posture service did not answer. <button class="btn sm" type="button" @click="loadUnmatched">Retry</button></div>
          <div v-else-if="!unmatched.length" class="empty-state"><b>Nothing unmatched</b>Every imported scan result was matched to a known host.</div>
        </section>

        <!-- Scanners -->
        <section v-else-if="tab === 'scanners'" class="card">
          <div class="tw"><table class="t">
            <thead><tr><th>Scanner</th><th>Kind</th><th>Import formats</th><th>State</th><th>Last run</th><th>Last result</th><th class="r"><span class="vh">Actions</span></th></tr></thead>
            <tbody>
              <tr v-for="sc in scanners" :key="sc.name">
                <td><span class="nm">{{ sc.name }}</span></td>
                <td><span class="tag">{{ sc.kind }}</span></td>
                <td><template v-if="sc.accepts_upload"><span v-for="fm in sc.formats || []" :key="fm" class="tag" style="margin-right:4px">{{ fm }}</span></template><span v-else class="muted">No upload</span></td>
                <td><span v-if="sc.enabled" class="b ok">Enabled</span><span v-else class="b off">Disabled</span></td>
                <td class="muted">{{ sc.last_run_at ? ago(sc.last_run_at) : 'Never' }}</td>
                <td><span v-if="sc.last_status" class="b" :class="scanKind(sc.last_status)">{{ sc.last_status }}</span><span v-else class="muted">—</span></td>
                <td class="r"><button v-if="auth.isAdmin && sc.accepts_upload" class="btn sm" type="button" @click="openImport(sc.name)">Import results</button></td>
              </tr>
            </tbody>
          </table></div>
          <div v-if="sLoading" class="empty-state">Loading…</div>
          <div v-else-if="sDown" class="empty-state"><b>Scanners are not available</b>The posture service did not answer. <button class="btn sm" type="button" @click="loadScanners">Retry</button></div>
          <div v-else-if="!scanners.length" class="empty-state"><b>No scanners registered</b></div>
        </section>

        <!-- Feeds -->
        <section v-else class="card">
          <div class="tw"><table class="t">
            <thead><tr><th>Version</th><th>Generated</th><th>Imported</th><th>Signer</th><th>Sources</th><th>State</th><th class="r"><span class="vh">Actions</span></th></tr></thead>
            <tbody>
              <tr v-for="f in feeds" :key="f.id">
                <td class="mono">{{ f.version }}</td>
                <td class="muted">{{ f.generated_at ? new Date(f.generated_at).toLocaleDateString() : '—' }}</td>
                <td><span class="muted">{{ f.imported_at ? new Date(f.imported_at).toLocaleString() : '—' }}</span><span class="s2">{{ f.imported_by || '' }}</span></td>
                <td class="mono" style="font-size:12px">{{ f.signed_by_key || '—' }}</td>
                <td><span v-for="s in f.sources" :key="s.name" class="tag" style="margin-right:4px">{{ s.name }} {{ s.entries.toLocaleString() }}</span></td>
                <td><span v-if="f.active" class="b ok">Active</span><span v-else class="b off">Inactive</span></td>
                <td class="r"><button v-if="auth.isAdmin && !f.active" class="btn sm" type="button" :disabled="activating === f.id" @click="activate(f)">{{ activating === f.id ? 'Activating…' : 'Activate' }}</button></td>
              </tr>
            </tbody>
          </table></div>
          <div v-if="!feeds.length && !loading" class="empty-state"><b>No feed bundles</b>Import a signed bundle to enable vulnerability matching.</div>
        </section>
      </div>
    </div>

    <!-- Finding drawer -->
    <div v-if="fDetail" class="modal-overlay" @click.self="fDetail = null">
      <div class="modal" role="dialog" :aria-label="`Finding ${fDetail.cve_id}`" style="width:min(640px,96vw)">
        <div class="modal-header"><span><span class="mono">{{ fDetail.cve_id }}</span> <span class="b" :class="sevKind(fDetail.severity)" style="margin-left:8px">{{ cap(fDetail.severity) }}</span> <span v-if="fDetail.kev" class="b bad" style="margin-left:4px">KEV</span></span><button class="btn sm ib g" type="button" aria-label="Close" @click="fDetail = null">×</button></div>
        <div class="modal-body" style="display:flex;flex-direction:column;gap:16px">
          <p v-if="fDetail.summary" style="margin:0">{{ fDetail.summary }}</p>
          <dl class="kv">
            <dt>Host</dt><dd><router-link class="lk" :to="`/posture/hosts/${fDetail.host_id}`">{{ fDetail.hostname || fDetail.host_id }}</router-link></dd>
            <dt>Package</dt><dd class="mono">{{ fDetail.package }} {{ fDetail.installed_version }}</dd>
            <dt>Fixed in</dt><dd class="mono">{{ fDetail.fixed_version || '—' }}</dd>
            <dt>Risk score</dt><dd><b>{{ fDetail.risk_score ?? '—' }}</b> / 100</dd>
            <dt>CVSS</dt><dd>{{ fDetail.cvss ?? '—' }}</dd>
            <dt>EPSS</dt><dd>{{ fDetail.epss != null ? fDetail.epss : '—' }}<span v-if="fDetail.epss_percentile != null" class="muted"> (percentile {{ fDetail.epss_percentile }})</span></dd>
            <dt>Sources</dt><dd><span v-for="s in fDetail.sources || []" :key="s" class="tag" style="margin-right:4px">{{ s }}</span></dd>
            <dt>First seen</dt><dd>{{ fDetail.first_seen ? new Date(fDetail.first_seen).toLocaleString() : '—' }}</dd>
            <dt>Last seen</dt><dd>{{ fDetail.last_seen ? new Date(fDetail.last_seen).toLocaleString() : '—' }}</dd>
            <dt>Status</dt><dd><span class="b" :class="fStatusKind(fDetail.status)">{{ cap(fDetail.status) }}</span></dd>
          </dl>
          <div v-if="(fDetail.references || []).length">
            <h3 class="ct" style="margin-bottom:8px">References</h3>
            <ul style="margin:0;padding-left:18px"><li v-for="u in fDetail.references" :key="u"><a v-if="isSafeUrl(u)" class="lk" :href="u" target="_blank" rel="noopener noreferrer">{{ u }}</a><span v-else class="mono">{{ u }}</span></li></ul>
          </div>
          <div v-if="(fDetail.history || []).length">
            <h3 class="ct" style="margin-bottom:8px">History</h3>
            <div class="card tw"><table class="t"><thead><tr><th>When</th><th>Event</th><th>By</th></tr></thead>
              <tbody><tr v-for="(h, i) in fDetail.history" :key="i"><td class="muted">{{ new Date(h.at).toLocaleString() }}</td><td>{{ h.event }}</td><td>{{ h.by || '—' }}</td></tr></tbody></table></div>
          </div>
          <div v-if="auth.isAdmin && reasonFor" class="stack" style="gap:8px">
            <label for="f-reason" class="ct">Reason for {{ reasonFor === 'accepted' ? 'accepting this risk' : 'reopening' }}</label>
            <textarea id="f-reason" v-model="reason" class="in" rows="3" style="height:auto;padding:8px 10px" placeholder="Why?"></textarea>
          </div>
          <div v-if="fError" class="callout" style="--c:var(--bad)" role="alert">{{ fError }}</div>
        </div>
        <div class="modal-footer">
          <template v-if="auth.isAdmin">
            <template v-if="reasonFor">
              <button class="btn" type="button" @click="reasonFor = ''">Cancel</button>
              <button class="btn p" type="button" :disabled="!reason.trim() || settingStatus" @click="submitStatus">{{ settingStatus ? 'Saving…' : 'Confirm' }}</button>
            </template>
            <template v-else>
              <button v-if="fDetail.status === 'open' && fDetail.package" class="btn p" type="button" @click="router.push({ path: '/patching', query: { finding: fDetail.id, label: `${fDetail.cve_id} on ${fDetail.hostname || fDetail.host_id}` } })">Patch</button>
              <button v-if="fDetail.status === 'open'" class="btn" type="button" @click="startStatus('accepted')">Accept risk</button>
              <button v-else-if="fDetail.status === 'accepted'" class="btn" type="button" @click="startStatus('open')">Reopen</button>
            </template>
          </template>
          <button class="btn p" type="button" @click="fDetail = null">Close</button>
        </div>
      </div>
    </div>

    <!-- Assign unmatched -->
    <div v-if="assigning" class="modal-overlay" @click.self="assigning = null">
      <div class="modal" role="dialog" aria-label="Assign to host" style="width:min(480px,96vw)">
        <div class="modal-header"><span>Assign to host</span><button class="btn sm ib g" type="button" aria-label="Close" @click="assigning = null">×</button></div>
        <div class="modal-body stack" style="gap:12px">
          <p class="muted" style="margin:0">{{ assigning.scanner }} reported <b>{{ assigning.reported_host || assigning.reported_ip || 'an unknown host' }}</b> with {{ assigning.cve_count }} CVEs.</p>
          <label for="as-host" class="ct">Host</label>
          <select id="as-host" v-model="assignHost" class="in"><option value="">Select a host…</option><option v-for="h in hostOptions" :key="h.id" :value="h.id">{{ h.name }}</option></select>
          <div v-if="assignError" class="callout" style="--c:var(--bad)" role="alert">{{ assignError }}</div>
        </div>
        <div class="modal-footer"><button class="btn" type="button" @click="assigning = null">Cancel</button><button class="btn p" type="button" :disabled="!assignHost || assignBusy" @click="submitAssign">{{ assignBusy ? 'Assigning…' : 'Assign' }}</button></div>
      </div>
    </div>

    <!-- Import scan results -->
    <div v-if="importing" class="modal-overlay" @click.self="importing = false">
      <div class="modal" role="dialog" aria-label="Import scan results" style="width:min(480px,96vw)">
        <div class="modal-header"><span>Import scan results</span><button class="btn sm ib g" type="button" aria-label="Close" @click="importing = false">×</button></div>
        <div class="modal-body stack" style="gap:12px">
          <label for="im-sc" class="ct">Scanner</label>
          <select id="im-sc" v-model="imScanner" class="in"><option value="">Select a scanner…</option><option v-for="sc in uploadScanners" :key="sc.name" :value="sc.name">{{ sc.name }}{{ sc.formats?.length ? ` (${sc.formats.join(', ')})` : '' }}</option></select>
          <label for="im-host" class="ct">Host <span class="muted" style="font-weight:400">(optional)</span></label>
          <select id="im-host" v-model="imHost" class="in"><option value="">Match from the file</option><option v-for="h in hostOptions" :key="h.id" :value="h.id">{{ h.name }}</option></select>
          <label for="im-file" class="ct">File</label>
          <input id="im-file" type="file" class="in" style="height:auto;padding:6px" @change="imFile = ($event.target as HTMLInputElement).files?.[0] || null" />
          <div v-if="imError" class="callout" style="--c:var(--bad)" role="alert">{{ imError }}</div>
          <div v-if="imResult" class="callout" style="--c:var(--ok)" role="status">Scan {{ imResult.scan_id }}: {{ imResult.findings_created }} created, {{ imResult.findings_updated }} updated, {{ imResult.unmatched }} unmatched.</div>
        </div>
        <div class="modal-footer"><button class="btn" type="button" @click="importing = false">Close</button><button class="btn p" type="button" :disabled="!imScanner || !imFile || imBusy" @click="submitImport">{{ imBusy ? 'Importing…' : 'Import' }}</button></div>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { SEVS, ago, cap, detailOf, fStatusKind, isDown, sevKind, updatesKnown, type PostureHost } from '@/components/posture/posture'

interface Feed { id: string; version: string; generated_at?: string; imported_at?: string; imported_by?: string; signed_by_key?: string; active: boolean; sources: { name: string; entries: number }[] }

const auth = useAuthStore()
type Tab = 'findings' | 'unmatched' | 'scanners' | 'feeds'
const TABS: Tab[] = ['findings', 'unmatched', 'scanners', 'feeds']
const route = useRoute()
const router = useRouter()
// The host list moved to its own page; old links to ?tab=hosts land there.
if (route.query.tab === 'hosts') router.replace('/posture/hosts')
const initTab = String(route.query.tab || 'findings') as Tab
const tab = ref<Tab>(TABS.includes(initTab) ? initTab : 'findings')
// Tab lives in the URL (?tab=hosts) like Automation; Findings is the default and is omitted.
function setTab(t: Tab) {
  tab.value = t
  const q = { ...route.query }
  if (t === 'findings') delete q.tab; else q.tab = t
  router.replace({ query: q })
}
watch(() => route.query.tab, (t) => { const v = String(t || 'findings') as Tab; if (TABS.includes(v) && v !== tab.value) tab.value = v })
watch(tab, (t) => { if (t === 'unmatched' && !uLoaded.value) loadUnmatched(); if (t === 'scanners' && !sLoaded.value) loadScanners() })
const hosts = ref<PostureHost[]>([])
const feeds = ref<Feed[]>([])
const summary = ref<any>(null)
const loading = ref(true)
const unavailable = ref(false)
const error = ref('')
const uploading = ref(false)
const activating = ref('')
const notice = ref('')

// ---- Findings -------------------------------------------------------------
const PAGE = 50
const scanKind = (s: string) => (/ok|success|done|complete/i.test(s) ? 'ok' : /fail|error/i.test(s) ? 'bad' : 'pend')
const findings = ref<any[]>([])
const fTotal = ref(0)
const fOffset = ref(0)
const fLoading = ref(false)
const fDown = ref(false)
const fq = ref(''); const fSeverity = ref(''); const fStatus = ref(''); const fHost = ref(String(route.query.host || '')); const fKev = ref(false)
const fSort = ref<'risk' | 'last_seen'>('risk')
const fSummary = ref<any>(null)
const fFiltered = computed(() => !!(fq.value || fSeverity.value || fStatus.value || fHost.value || fKev.value))
const hostOptions = ref<{ id: string; name: string }[]>([])
async function loadHostOptions() {
  try { hostOptions.value = ((await api.get('/hosts')).data || []).map((h: any) => ({ id: h.id, name: h.hostname || h.name || h.id })) } catch { hostOptions.value = [] }
}
async function loadFindings() {
  fLoading.value = true; fDown.value = false
  try {
    const params: Record<string, any> = { sort: fSort.value, limit: PAGE, offset: fOffset.value }
    if (fq.value.trim()) params.q = fq.value.trim()
    if (fSeverity.value) params.severity = fSeverity.value
    if (fStatus.value) params.status = fStatus.value
    if (fHost.value) params.host_id = fHost.value
    if (fKev.value) params.kev = true
    const r = (await api.get('/posture/findings', { params })).data
    findings.value = r.items || []; fTotal.value = r.total ?? findings.value.length
  } catch (e: any) {
    findings.value = []; fTotal.value = 0
    if (isDown(e)) fDown.value = true; else error.value = detailOf(e, 'Could not load findings.')
  } finally { fLoading.value = false }
}
async function loadFSummary() { try { fSummary.value = (await api.get('/posture/findings/summary')).data } catch { fSummary.value = null } }
let fTimer: ReturnType<typeof setTimeout> | null = null
function onFilter() { fOffset.value = 0; if (fTimer) clearTimeout(fTimer); fTimer = setTimeout(loadFindings, 250) }
function toggleSort() { fSort.value = fSort.value === 'risk' ? 'last_seen' : 'risk'; fOffset.value = 0; loadFindings() }
function page(d: number) { fOffset.value = Math.max(0, fOffset.value + d * PAGE); loadFindings() }

const fDetail = ref<any>(null)
// References come from imported scan files: only http(s) may become a link,
// anything else (javascript:, data:, …) is shown as plain text.
function isSafeUrl(u: unknown): boolean {
  if (typeof u !== 'string') return false
  try { const p = new URL(u); return p.protocol === 'https:' || p.protocol === 'http:' } catch { return false }
}
const fError = ref('')
const reasonFor = ref<'' | 'accepted' | 'open'>('')
const reason = ref('')
const settingStatus = ref(false)
async function openFinding(f: any) {
  fDetail.value = { ...f }; fError.value = ''; reasonFor.value = ''; reason.value = ''
  try { fDetail.value = (await api.get(`/posture/findings/${f.id}`)).data } catch (e: any) { fError.value = detailOf(e, 'Could not load finding detail.') }
}
function startStatus(s: 'accepted' | 'open') { reasonFor.value = s; reason.value = ''; fError.value = '' }
async function submitStatus() {
  if (!fDetail.value || !reasonFor.value) return
  settingStatus.value = true; fError.value = ''
  try {
    await api.post(`/posture/findings/${fDetail.value.id}/status`, { status: reasonFor.value, reason: reason.value.trim() })
    const id = fDetail.value.id
    reasonFor.value = ''
    await Promise.all([loadFindings(), loadFSummary(), reload()])
    fDetail.value = (await api.get(`/posture/findings/${id}`)).data
  } catch (e: any) { fError.value = detailOf(e, 'Could not change the finding status.') } finally { settingStatus.value = false }
}

// ---- Unmatched ------------------------------------------------------------
const unmatched = ref<any[]>([])
const uLoading = ref(false); const uDown = ref(false); const uLoaded = ref(false)
async function loadUnmatched() {
  uLoading.value = true; uDown.value = false
  try { unmatched.value = (await api.get('/posture/unmatched')).data?.items || []; uLoaded.value = true }
  catch (e: any) { unmatched.value = []; if (isDown(e)) uDown.value = true; else error.value = detailOf(e, 'Could not load unmatched results.') }
  finally { uLoading.value = false }
}
const assigning = ref<any>(null); const assignHost = ref(''); const assignBusy = ref(false); const assignError = ref('')
function openAssign(u: any) { assigning.value = u; assignHost.value = ''; assignError.value = '' }
async function submitAssign() {
  if (!assigning.value || !assignHost.value) return
  assignBusy.value = true; assignError.value = ''
  try {
    const r = (await api.post(`/posture/unmatched/${assigning.value.id}/assign`, { host_id: assignHost.value })).data
    notice.value = `Assigned: ${r?.created_findings ?? 0} findings created.`
    assigning.value = null
    await Promise.all([loadUnmatched(), loadFindings(), loadFSummary()])
  } catch (e: any) { assignError.value = detailOf(e, 'Assignment failed.') } finally { assignBusy.value = false }
}

// ---- Scanners -------------------------------------------------------------
const scanners = ref<any[]>([])
const sLoading = ref(false); const sDown = ref(false); const sLoaded = ref(false)
const scanning = ref(false)
const uploadScanners = computed(() => scanners.value.filter((x) => x.accepts_upload))
async function loadScanners() {
  sLoading.value = true; sDown.value = false
  try { const d = (await api.get('/posture/scanners')).data; scanners.value = Array.isArray(d) ? d : d?.items || []; sLoaded.value = true }
  catch (e: any) { scanners.value = []; if (isDown(e)) sDown.value = true; else error.value = detailOf(e, 'Could not load scanners.') }
  finally { sLoading.value = false }
}
async function runNative() {
  scanning.value = true; error.value = ''; notice.value = ''
  try { const r = (await api.post('/posture/scanners/native/run', {})).data; notice.value = `Native scan queued${r?.queued != null ? ` for ${r.queued} host${r.queued === 1 ? '' : 's'}` : ''}.`; await loadScanners() }
  catch (e: any) { error.value = detailOf(e, 'Could not start the native scan.') } finally { scanning.value = false }
}
const importing = ref(false); const imScanner = ref(''); const imHost = ref(''); const imFile = ref<File | null>(null)
const imBusy = ref(false); const imError = ref(''); const imResult = ref<any>(null)
function openImport(name = '') { importing.value = true; imScanner.value = name; imHost.value = ''; imFile.value = null; imError.value = ''; imResult.value = null }
async function submitImport() {
  if (!imScanner.value || !imFile.value) return
  imBusy.value = true; imError.value = ''; imResult.value = null
  try {
    const fd = new FormData(); fd.append('file', imFile.value); fd.append('scanner', imScanner.value)
    if (imHost.value) fd.append('host_id', imHost.value)
    imResult.value = (await api.post('/posture/scans/import', fd, { headers: { 'Content-Type': 'multipart/form-data' } })).data
    await Promise.all([loadFindings(), loadFSummary(), loadUnmatched(), loadScanners(), reload()])
  } catch (e: any) { imError.value = detailOf(e, 'Import failed.') } finally { imBusy.value = false }
}

async function reload() {
  loading.value = true; error.value = ''; unavailable.value = false
  try {
    const [s, h, f] = await Promise.all([api.get('/posture/summary'), api.get('/posture/hosts'), api.get('/posture/feeds')])
    summary.value = s.data; hosts.value = h.data || []; feeds.value = f.data || []
  } catch (e: any) {
    if (isDown(e)) unavailable.value = true
    else error.value = detailOf(e, 'Could not load posture data.')
  } finally { loading.value = false }
}
onMounted(() => { reload(); loadFindings(); loadFSummary(); loadUnmatched(); loadScanners(); loadHostOptions() })

// Hosts whose facts are old, failed or incomplete: the same rule as the hosts page's filter.
const attention = computed(() => hosts.value.filter((h) => h.status === 'stale' || h.status === 'failed' || !updatesKnown(h)).length)
// A dashboard tile opens the findings list with its filter set.
function showFindings(f: { severity?: string; kev?: boolean }) {
  fq.value = ''; fStatus.value = 'open'; fHost.value = ''
  fSeverity.value = f.severity || ''; fKev.value = !!f.kev
  setTab('findings'); onFilter()
  document.getElementById('f-q')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

async function onFile(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploading.value = true; error.value = ''
  try {
    const fd = new FormData(); fd.append('file', file)
    await api.post('/posture/feeds/import', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
    await reload()
  } catch (e: any) { error.value = detailOf(e, 'Import failed.') }   // 400 detail shown verbatim
  finally { uploading.value = false; input.value = '' }
}
async function activate(f: Feed) {
  activating.value = f.id; error.value = ''
  try { await api.post(`/posture/feeds/${f.id}/activate`); await reload() }
  catch (e: any) { error.value = detailOf(e, 'Activation failed.') } finally { activating.value = '' }
}
</script>

<style scoped>
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; }
/* Tiles are buttons or links into a filtered list. */
.tile { font: inherit; color: inherit; text-align: left; text-decoration: none; cursor: pointer; display: flex; flex-direction: column; transition: border-color .15s; }
.tile:hover { border-color: var(--link); }
.tile:focus-visible { outline: 2px solid var(--link); outline-offset: 2px; }
.stat .d { font-size: 12.5px; color: var(--muted); }
.is-dis { opacity: .6; pointer-events: none; }
tr[tabindex]:focus-visible { outline: 2px solid var(--link); outline-offset: -2px; }
</style>
