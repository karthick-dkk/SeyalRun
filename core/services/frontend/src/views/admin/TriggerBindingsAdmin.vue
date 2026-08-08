<template>
  <div>
    <div class="card">
      <div class="card-header">
        Zabbix Trigger Bindings
        <div style="display:flex;gap:8px">
          <button class="btn btn-sm" @click="openFromProblem">⚡ From Live Problem</button>
          <button class="btn btn-primary btn-sm" @click="openCreate">+ Trigger Binding</button>
        </div>
      </div>
      <div style="padding:10px 16px;font-size:12px;color:var(--text2);border-bottom:1px solid var(--border)">
        Each binding maps a Zabbix trigger/event to a SeyalRun job template. When Zabbix fires a
        matching event via the webhook, a job run is automatically dispatched. Use the
        <strong>Manual Trigger</strong> button to also run it on-demand. Global-script wiring
        snippets (SSH link, webhook, dropdown picker) are on the
        <router-link to="/admin/integration" style="color:var(--accent2)">Integration</router-link> page.
      </div>
      <table class="table">
        <thead>
          <tr><th>Name</th><th>Job Template</th><th>Trigger</th><th>Host Group</th><th>Min Severity</th><th>Post Result</th><th>Enabled</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="b in bindings" :key="b.id">
            <td style="font-weight:600">{{ b.name }}</td>
            <td style="color:var(--text2)">{{ templateName(b.job_template_id) }}</td>
            <td>
              <template v-if="b.zabbix_triggerid">
                <div>{{ b.zabbix_trigger_name || b.zabbix_triggerid }}</div>
                <div style="font-family:monospace;font-size:11px;color:var(--text2)">{{ b.zabbix_triggerid }}</div>
              </template>
              <span v-else style="color:var(--text2)">— any —</span>
            </td>
            <td style="color:var(--text2)">{{ b.zabbix_host_group || '— any —' }}</td>
            <td><span class="badge badge-blue">{{ SEVERITY_LABELS[b.severity_min] ?? b.severity_min }}</span></td>
            <td style="text-align:center">{{ b.post_result_to_zabbix ? '✓' : '—' }}</td>
            <td><span :class="b.enabled ? 'badge badge-green' : 'badge badge-gray'">{{ b.enabled ? 'Enabled' : 'Disabled' }}</span></td>
            <td>
              <div style="display:flex;gap:8px;justify-content:flex-end">
                <button class="btn-pill btn-pill-outline" @click="openRun(b)">▶ Run</button>
                <button class="btn-pill btn-pill-outline" @click="openEdit(b)">✎ Edit</button>
                <button class="btn-pill btn-pill-outline" style="color:var(--danger);border-color:var(--danger)" @click="removeBinding(b)">🗑</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!bindings.length && !bindingsLoading" style="padding:24px;text-align:center;color:var(--text2)">No trigger bindings yet.</div>
      <div v-if="bindingsLoading" style="padding:24px;text-align:center;color:var(--text2)">Loading…</div>
    </div>

    <!-- ── Trigger Binding modal ─────────────────────────────────────────── -->
    <div v-if="modal.open" class="modal-overlay" @click.self="modal.open = false">
      <div class="modal" style="width:50vw; min-width:480px; max-width:820px">
        <div class="modal-header">{{ modal.id ? 'Edit' : 'Create' }} Trigger Binding</div>
        <div class="modal-body">
          <label class="form-label">Name</label>
          <input v-model="modal.name" class="input" placeholder="Binding name" />

          <label class="form-label" style="margin-top:12px">Job Template</label>
          <AsyncPicker v-model="templatePick" :search-fn="searchTemplates" :multiple="false"
                       placeholder="Search job templates…" />

          <label class="form-label" style="margin-top:12px">Zabbix Trigger (optional, blank = any)</label>
          <AsyncPicker v-model="triggerPick" :search-fn="searchTriggers" :multiple="false"
                       placeholder="Search triggers by name…" />

          <label class="form-label" style="margin-top:12px">Zabbix Host Group filter (optional, blank = any)</label>
          <input v-model="modal.zabbix_host_group" class="input" placeholder="e.g. Linux servers" />

          <label class="form-label" style="margin-top:12px">Minimum Severity</label>
          <select v-model="modal.severity_min" class="input">
            <option :value="0">Not classified (0)</option>
            <option :value="1">Information (1)</option>
            <option :value="2">Warning (2)</option>
            <option :value="3">Average (3)</option>
            <option :value="4">High (4)</option>
            <option :value="5">Disaster (5)</option>
          </select>

          <div style="display:flex;gap:16px;margin-top:16px">
            <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
              <input type="checkbox" v-model="modal.post_result_to_zabbix" />
              <span class="form-label" style="margin:0">Post result back to Zabbix</span>
            </label>
            <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
              <input type="checkbox" v-model="modal.enabled" />
              <span class="form-label" style="margin:0">Enabled</span>
            </label>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn" @click="modal.open = false">Cancel</button>
          <button class="btn btn-primary" @click="saveBinding" :disabled="!modal.name || !modal.job_template_id">Save</button>
        </div>
      </div>
    </div>

    <!-- ── Create from Live Problem: search, then hand off to the modal above ── -->
    <div v-if="fromProblem.open" class="modal-overlay" @click.self="fromProblem.open = false">
      <div class="modal" style="width:50vw; min-width:420px; max-width:760px">
        <div class="modal-header">Create Binding from Live Problem</div>
        <div class="modal-body" style="min-height:340px">
          <label class="form-label">Live Problem</label>
          <AsyncPicker v-model="problemPick" :search-fn="searchLiveProblems" :multiple="false"
                       placeholder="Search current firing problems…" />
        </div>
        <div class="modal-footer">
          <button class="btn" @click="fromProblem.open = false">Cancel</button>
          <button class="btn btn-primary" :disabled="!problemPick.length" @click="useSelectedProblem">Use this problem →</button>
        </div>
      </div>
    </div>

    <!-- ── Run: resolve/choose a host, optional connectivity test, then dispatch ── -->
    <div v-if="run.open" class="modal-overlay" @click.self="run.open = false">
      <div class="modal" style="width:50vw; min-width:460px; max-width:720px">
        <div class="modal-header">Run "{{ run.binding?.name }}"</div>
        <div class="modal-body">
          <label class="form-label">Target host</label>
          <div style="display:flex;gap:8px;margin-bottom:12px">
            <button class="btn-pill" :class="run.mode === 'default' ? 'btn-pill-solid' : 'btn-pill-outline'"
                    :disabled="!run.binding?.zabbix_triggerid" @click="run.mode = 'default'">
              Default host (from Zabbix)
            </button>
            <button class="btn-pill" :class="run.mode === 'choose' ? 'btn-pill-solid' : 'btn-pill-outline'"
                    @click="run.mode = 'choose'">
              Choose host
            </button>
          </div>

          <template v-if="!run.binding?.zabbix_triggerid">
            <div style="font-size:12px;color:var(--text2);margin-bottom:10px">
              This binding has no specific trigger (host-group binding) — pick a host below.
            </div>
          </template>
          <template v-else-if="run.mode === 'default'">
            <div v-if="run.hostLoading" style="font-size:13px;color:var(--text2)">Resolving host from Zabbix…</div>
            <div v-else-if="run.hostError" style="font-size:12px;color:var(--danger)">{{ run.hostError }}</div>
            <div v-else-if="run.resolvedHost" style="font-size:13px;padding:10px 12px;background:var(--bg3);border-radius:var(--radius)">
              <strong>{{ run.resolvedHost.name }}</strong>
              <span style="color:var(--text2);margin-left:8px">{{ run.resolvedHost.ip }}</span>
            </div>
          </template>
          <template v-else>
            <AsyncPicker v-model="run.hostPick" :search-fn="searchHosts" :multiple="false" placeholder="Search hosts…" />
          </template>

          <div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">
            <button class="btn" :disabled="!effectiveHostId || run.testing" @click="testConnection">
              {{ run.testing ? 'Testing…' : '🔌 Test Connection' }}
            </button>
            <div v-if="run.testResult" style="margin-top:10px">
              <div v-if="run.testResult.ok" style="font-size:12.5px;color:var(--accent)">✓ Connected</div>
              <template v-else>
                <div style="font-size:12.5px;color:var(--danger)">✗ Connection failed</div>
                <pre style="margin-top:6px;padding:10px 12px;background:var(--bg3);border-radius:var(--radius);font-size:11.5px;color:var(--text2);white-space:pre-wrap;max-height:160px;overflow-y:auto">{{ run.testResult.output }}</pre>
              </template>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn" @click="run.open = false">Cancel</button>
          <button class="btn btn-primary" :disabled="!effectiveHostId || run.testResult?.ok === false || run.running"
                  @click="confirmRun">
            {{ run.running ? 'Starting…' : '▶ Run' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import api from '@/api/client'
import AsyncPicker, { type PickerItem } from '@/components/common/AsyncPicker.vue'

const router = useRouter()

const SEVERITY_LABELS: Record<number, string> = {
  0: 'Not classified', 1: 'Information', 2: 'Warning', 3: 'Average', 4: 'High', 5: 'Disaster',
}

const bindings = ref<any[]>([])
const templates = ref<any[]>([])
const bindingsLoading = ref(false)

const modal = reactive({
  open: false, id: '', name: '', job_template_id: '',
  zabbix_triggerid: '', zabbix_trigger_name: '', zabbix_host_group: '',
  severity_min: 0, post_result_to_zabbix: true, enabled: true,
})

// Single-select trigger picker for the modal above — kept as its own ref
// (AsyncPicker's v-model is always an array of PickerItem) and synced into
// modal.zabbix_triggerid/zabbix_trigger_name, which stay the source of
// truth saveBinding() actually posts.
const triggerPick = ref<PickerItem[]>([])
watch(triggerPick, (v) => {
  const t = v[0]
  modal.zabbix_triggerid = t?.id || ''
  modal.zabbix_trigger_name = t?.label || ''
})

async function searchTriggers(query: string): Promise<PickerItem[]> {
  return api.get('/triggers/search', { params: { q: query, limit: 20 } }).then(r => r.data)
}

const templatePick = ref<PickerItem[]>([])
watch(templatePick, (v) => { modal.job_template_id = v[0]?.id || '' })

async function searchTemplates(query: string): Promise<PickerItem[]> {
  const q = query.trim().toLowerCase()
  return templates.value.filter((t: any) => !q || t.name.toLowerCase().includes(q))
    .slice(0, 20).map((t: any) => ({ id: t.id, label: t.name }))
}

async function loadBindings() {
  bindingsLoading.value = true
  try {
    const [b, t] = await Promise.all([
      api.get('/trigger-bindings').then(r => r.data),
      api.get('/job-templates').then(r => r.data),
    ])
    bindings.value = b
    templates.value = t
  } finally {
    bindingsLoading.value = false
  }
}

const templateName = (id: string) => templates.value.find(t => t.id === id)?.name || id

function openCreate() {
  Object.assign(modal, {
    open: true, id: '', name: '', job_template_id: '',
    zabbix_triggerid: '', zabbix_trigger_name: '', zabbix_host_group: '',
    severity_min: 0, post_result_to_zabbix: true, enabled: true,
  })
  triggerPick.value = []
  templatePick.value = []
}
function openEdit(b: any) {
  Object.assign(modal, {
    open: true, id: b.id, name: b.name, job_template_id: b.job_template_id,
    zabbix_triggerid: b.zabbix_triggerid || '', zabbix_trigger_name: b.zabbix_trigger_name || '',
    zabbix_host_group: b.zabbix_host_group || '',
    severity_min: b.severity_min, post_result_to_zabbix: b.post_result_to_zabbix, enabled: b.enabled,
  })
  // Populated straight from the binding's own stored name — no live
  // /triggers/search call here, so opening Edit never hits Zabbix (and
  // still shows the right label even if that trigger's since been deleted).
  triggerPick.value = b.zabbix_triggerid
    ? [{ id: b.zabbix_triggerid, label: b.zabbix_trigger_name || b.zabbix_triggerid, sublabel: '' }]
    : []
  templatePick.value = b.job_template_id
    ? [{ id: b.job_template_id, label: templateName(b.job_template_id) }]
    : []
}
async function saveBinding() {
  const body = {
    name: modal.name,
    job_template_id: modal.job_template_id,
    zabbix_triggerid: modal.zabbix_triggerid || null,
    zabbix_trigger_name: modal.zabbix_trigger_name || null,
    zabbix_host_group: modal.zabbix_host_group || null,
    severity_min: modal.severity_min,
    post_result_to_zabbix: modal.post_result_to_zabbix,
    enabled: modal.enabled,
  }
  if (modal.id) {
    await api.put(`/trigger-bindings/${modal.id}`, body)
  } else {
    await api.post('/trigger-bindings', body)
  }
  modal.open = false
  await loadBindings()
}

// ── Create from Live Problem ────────────────────────────────────────────
const fromProblem = reactive({ open: false })
const problemPick = ref<PickerItem[]>([])

async function searchLiveProblems(query: string): Promise<PickerItem[]> {
  return api.get('/triggers/live-problems', { params: { q: query, limit: 20 } }).then(r => r.data)
}

function openFromProblem() {
  problemPick.value = []
  fromProblem.open = true
}

function useSelectedProblem() {
  const p: any = problemPick.value[0]
  if (!p) return
  fromProblem.open = false
  Object.assign(modal, {
    open: true, id: '', name: `Auto: ${p.label}`, job_template_id: '',
    zabbix_triggerid: p.triggerid || '', zabbix_trigger_name: p.label || '',
    zabbix_host_group: '',
    severity_min: p.severity || 0, post_result_to_zabbix: true, enabled: true,
  })
  triggerPick.value = p.triggerid
    ? [{ id: p.triggerid, label: p.label, sublabel: p.host_name || '' }]
    : []
  templatePick.value = []
}
async function removeBinding(b: any) {
  if (!confirm(`Delete trigger binding "${b.name}"?`)) return
  await api.delete(`/trigger-bindings/${b.id}`)
  await loadBindings()
}

// ── Run: resolve/choose a host, optional connectivity test, then dispatch ──
// Replaces the old manualTrigger(), which posted { binding_id } with NO
// host — the executor then silently ran the script/playbook INSIDE the
// automation-service container itself (its "no target host" fallback),
// instead of on any real target. Confirmed live: a "Docker ps" bash_script
// binding run this way failed with "sudo: command not found" because it
// ran in a container that has no sudo, not on the intended host. A host is
// now mandatory — either resolved from the trigger's Zabbix host, or
// explicitly chosen — before Run is enabled at all.
const allHosts = ref<any[]>([])

const run = reactive({
  open: false, binding: null as any,
  mode: 'default' as 'default' | 'choose',
  hostLoading: false, hostError: '', resolvedHost: null as any,
  hostPick: [] as PickerItem[],
  testing: false, testResult: null as { ok: boolean; output: string } | null,
  running: false,
})

const effectiveHostId = computed(() =>
  run.mode === 'default' ? (run.resolvedHost?.id || '') : (run.hostPick[0]?.id || '')
)

async function loadAllHosts() {
  if (allHosts.value.length) return
  allHosts.value = await api.get('/hosts').then(r => r.data).catch(() => [])
}

async function searchHosts(query: string): Promise<PickerItem[]> {
  const q = query.trim().toLowerCase()
  return allHosts.value.filter((h: any) => !q || h.name.toLowerCase().includes(q) || (h.ip || '').includes(q))
    .slice(0, 20).map((h: any) => ({ id: h.id, label: h.name, sublabel: h.ip }))
}

async function openRun(b: any) {
  Object.assign(run, {
    open: true, binding: b, mode: b.zabbix_triggerid ? 'default' : 'choose',
    hostLoading: false, hostError: '', resolvedHost: null,
    testing: false, testResult: null, running: false,
  })
  run.hostPick = []
  await loadAllHosts()
  if (b.zabbix_triggerid) await resolveDefaultHost(b)
}

async function resolveDefaultHost(b: any) {
  run.hostLoading = true; run.hostError = ''; run.resolvedHost = null
  try {
    const { data } = await api.get('/triggers/host-for-trigger', { params: { triggerid: b.zabbix_triggerid } })
    const match = allHosts.value.find((h: any) => String(h.zabbix_hostid) === String(data.zabbix_hostid))
    if (!match) {
      run.hostError = `Zabbix host "${data.zabbix_host_name}" isn't linked to a SeyalRun host — choose one manually.`
      run.mode = 'choose'
    } else {
      run.resolvedHost = match
    }
  } catch (e: any) {
    run.hostError = e?.response?.data?.detail || 'Could not resolve host from Zabbix'
    run.mode = 'choose'
  } finally {
    run.hostLoading = false
  }
}

async function testConnection() {
  if (!effectiveHostId.value) return
  run.testing = true; run.testResult = null
  try {
    const { data } = await api.post('/test-connection', { host_id: effectiveHostId.value })
    run.testResult = data
  } catch (e: any) {
    run.testResult = { ok: false, output: e?.response?.data?.detail || 'Test request failed' }
  } finally {
    run.testing = false
  }
}

async function confirmRun() {
  if (!effectiveHostId.value || run.testResult?.ok === false) return
  run.running = true
  try {
    const { data } = await api.post('/triggers/manual', { binding_id: run.binding.id, host_id: effectiveHostId.value })
    run.open = false
    if (data.run_id) router.push(`/jobs/${data.run_id}`)
  } finally {
    run.running = false
  }
}

onMounted(loadBindings)
</script>
