<template>
  <div>
    <div class="card">
      <div class="card-header">
        Roles
        <button v-if="auth.isSuperAdmin" class="btn btn-primary btn-sm" @click="openCreate">+ Custom Role</button>
      </div>
      <table class="table">
        <thead><tr><th>Name</th><th>Type</th><th>Description</th><th>Access</th><th></th></tr></thead>
        <tbody>
          <tr v-for="r in roles" :key="r.id">
            <td style="font-weight:600;text-transform:capitalize">{{ r.name }}</td>
            <td>
              <span v-if="r.is_builtin" class="badge badge-gray">Built-in</span>
              <span v-else class="badge badge-blue">Custom</span>
            </td>
            <td style="color:var(--text2);font-size:12px">{{ r.description || '—' }}</td>
            <td style="font-size:11px;color:var(--text2)">{{ summary(r.permissions) }}</td>
            <td>
              <div style="display:flex;gap:8px;justify-content:flex-end">
                <button class="btn-pill btn-pill-outline" @click="openView(r)">{{ (r.is_builtin || !auth.isSuperAdmin) ? '👁 View' : '✎ Edit' }}</button>
                <button v-if="!r.is_builtin && auth.isSuperAdmin" class="btn-pill btn-pill-outline" style="color:var(--danger);border-color:var(--danger)" @click="removeRole(r)">🗑</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!roles.length && !loading" style="padding:32px;text-align:center;color:var(--text2)">No roles.</div>
    </div>

    <!-- Create / Edit / View role -->
    <div v-if="modal.open" class="modal-overlay" @click.self="modal.open = false">
      <div class="modal" style="width:min(620px,95vw)">
        <div class="modal-header">{{ modal.title }}<button class="btn btn-sm btn-icon" @click="modal.open = false">✕</button></div>
        <div class="modal-body">
          <div class="fp-field">
            <label class="fp-label">Name</label>
            <input v-model="form.name" class="fp-input" :disabled="modal.readonly || modal.editing" placeholder="e.g. deployer" />
          </div>
          <div class="fp-field">
            <label class="fp-label">Description</label>
            <input v-model="form.description" class="fp-input" :disabled="modal.readonly" placeholder="What is this role for?" />
          </div>

          <label class="opt-row">
            <input type="checkbox" v-model="form.all" :disabled="modal.readonly" />
            <span><b>Full / all-admin access</b> (every module + all hosts + reveal — like superadmin)</span>
          </label>
          <label class="opt-row" v-if="!form.all">
            <input type="checkbox" v-model="form.mfa" :disabled="modal.readonly" />
            <span><b>Allow MFA enrollment</b> (lets members of this role enable Authenticator/Email MFA)</span>
          </label>
          <template v-if="!form.all">
            <div class="fp-section-head">Module access</div>
            <div class="area-note">
              <b>Read</b> = view · <b>Write</b> = view + create/edit/delete · <b>Full</b> = Write
              <b>+</b> reveal secrets (Credentials) / all-hosts (Hosts). Full = Write for other modules.
            </div>
            <div class="area-grid">
              <div class="area-head"><span>Module</span><span>None</span><span>Read</span><span>Write</span><span>Full</span></div>
              <div v-for="a in AREAS" :key="a.key" class="area-row">
                <span class="area-name">{{ a.label }}<em v-if="a.capability" class="cap-hint">{{ a.capability === 'reveal' ? '· Full ⇒ reveal' : '· Full ⇒ all hosts' }}</em></span>
                <label class="area-radio"><input type="radio" :name="a.key" value="none" v-model="form.areas[a.key]" :disabled="modal.readonly" /></label>
                <label class="area-radio"><input type="radio" :name="a.key" value="read" v-model="form.areas[a.key]" :disabled="modal.readonly" /></label>
                <label class="area-radio"><input type="radio" :name="a.key" value="write" v-model="form.areas[a.key]" :disabled="modal.readonly" /></label>
                <label class="area-radio"><input type="radio" :name="a.key" value="full" v-model="form.areas[a.key]" :disabled="modal.readonly" /></label>
              </div>
            </div>
          </template>

          <div v-if="error" class="fp-error">{{ error }}</div>
        </div>
        <div class="modal-footer">
          <button class="btn" @click="modal.open = false">Close</button>
          <button v-if="!modal.readonly" class="btn btn-primary" :disabled="saving" @click="save">{{ saving ? 'Saving…' : (modal.editing ? 'Save' : 'Create') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useConfirm } from '@/composables/useConfirm'

const auth = useAuthStore()
const { confirm } = useConfirm()

// Module → API path segments (mirrors the gateway). Order is display order.
// `capability` marks the module whose "Full" level also grants a sensitive extra.
type ModuleDef = { key: string; label: string; segs: string[]; capability?: 'reveal' | 'all_hosts' }
const AREAS: ModuleDef[] = [
  { key: 'hosts', label: 'Hosts (view & connect)', segs: ['hosts', 'host-groups'], capability: 'all_hosts' },
  { key: 'assets', label: 'Assets (manage hosts)', segs: ['assets'] },
  { key: 'credentials', label: 'Credentials & Templates', segs: ['credentials', 'credential-templates'], capability: 'reveal' },
  { key: 'zones', label: 'Zones & Gateways', segs: ['zones'] },
  { key: 'users', label: 'Users, Groups & Roles', segs: ['users', 'roles'] },
  { key: 'authorizations', label: 'Authorizations', segs: ['authorizations'] },
  { key: 'api_tokens', label: 'API Tokens', segs: ['api-tokens'] },
  { key: 'automation', label: 'Automation (playbooks)', segs: ['projects', 'job-templates', 'schedules', 'secret-management-jobs'] },
  { key: 'jobs', label: 'Job Runs', segs: ['job-runs'] },
  { key: 'zabbix', label: 'Zabbix Integration', segs: ['trigger-bindings', 'triggers'] },
  { key: 'terminal', label: 'SSH Terminal & Sessions', segs: ['ssh'] },
  { key: 'recordings', label: 'Session Recordings', segs: ['recordings'] },
  { key: 'security', label: 'Security (filters / ACLs)', segs: ['command-groups', 'command-filters', 'login-acls'] },
  { key: 'housekeeping', label: 'Housekeeping', segs: ['housekeeping'] },
  { key: 'audit', label: 'Audit Logs', segs: ['audit'] },
  { key: 'log_backend', label: 'Log Backend', segs: ['log-backend'] },
  { key: 'dashboard', label: 'Dashboard / Metrics', segs: ['metrics'] },
]

const roles = ref<any[]>([])
const loading = ref(false)
const saving = ref(false)
const error = ref('')

const modal = reactive({ open: false, title: '', editing: false, readonly: false, id: '' })
const form = reactive<any>({ name: '', description: '', all: false, all_hosts: false, reveal: false, mfa: false, areas: {} as Record<string, string> })

function blankAreas() { const o: Record<string, string> = {}; AREAS.forEach(a => o[a.key] = 'none'); return o }

function summary(p: any): string {
  if (!p) return '—'
  if (p.all) return 'Full access'
  const w = (p.write || []).length, r = (p.read || []).includes('*') ? 'all' : (p.read || []).length
  return `write: ${w} · read: ${r}${p.reveal ? ' · reveal' : ''}${p.all_hosts ? ' · all-hosts' : ''}`
}

async function load() {
  loading.value = true
  try { roles.value = (await api.get('/roles')).data } finally { loading.value = false }
}

function permsToAreas(p: any): Record<string, string> {
  const areas = blankAreas()
  const w = new Set(p?.write || []), rd = p?.read || []
  const rAll = rd.includes('*'); const r = new Set(rd)
  for (const a of AREAS) {
    if (a.segs.some(s => w.has(s))) {
      // Full when the module's sensitive capability is also granted; else Write.
      const capFull = (a.capability === 'reveal' && !!p?.reveal) || (a.capability === 'all_hosts' && !!p?.all_hosts)
      areas[a.key] = capFull ? 'full' : 'write'
    } else if (rAll || a.segs.some(s => r.has(s))) areas[a.key] = 'read'
  }
  return areas
}

function openCreate() {
  Object.assign(modal, { open: true, title: 'Create Custom Role', editing: false, readonly: false, id: '' })
  Object.assign(form, { name: '', description: '', all: false, all_hosts: false, reveal: false, mfa: false, areas: blankAreas() })
  error.value = ''
}
function openView(r: any) {
  const ro = r.is_builtin || !auth.isSuperAdmin
  Object.assign(modal, { open: true, title: (ro ? 'Role' : 'Edit Role') + ' — ' + r.name, editing: true, readonly: ro, id: r.id })
  Object.assign(form, {
    name: r.name, description: r.description || '',
    all: !!r.permissions?.all, all_hosts: !!r.permissions?.all_hosts, reveal: !!r.permissions?.reveal,
    mfa: !!r.permissions?.mfa,
    areas: permsToAreas(r.permissions || {}),
  })
  error.value = ''
}

function buildPerms(): any {
  if (form.all) return { all: true, reveal: true, all_hosts: true, mfa: true }
  const read: string[] = [], write: string[] = []
  let reveal = false, all_hosts = false
  for (const a of AREAS) {
    const lvl = form.areas[a.key]
    if (lvl === 'full') {
      write.push(...a.segs)
      if (a.capability === 'reveal') reveal = true
      if (a.capability === 'all_hosts') all_hosts = true
    } else if (lvl === 'write') write.push(...a.segs)
    else if (lvl === 'read') read.push(...a.segs)
  }
  return { read, write, reveal, all_hosts, mfa: !!form.mfa }
}

async function save() {
  error.value = ''
  if (!modal.editing && !form.name.trim()) { error.value = 'Name is required.'; return }
  saving.value = true
  try {
    const perms = buildPerms()
    if (modal.editing) {
      await api.put(`/roles/${modal.id}`, { description: form.description, permissions: perms })
    } else {
      await api.post('/roles', { name: form.name.trim().toLowerCase(), description: form.description, permissions: perms })
    }
    modal.open = false
    load()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || 'Failed to save role'
  } finally { saving.value = false }
}

async function removeRole(r: any) {
  if (!await confirm(`Delete custom role "${r.name}"? Users lose this role.`, { title: 'Delete Role', danger: true, confirmLabel: 'Delete' })) return
  try { await api.delete(`/roles/${r.id}`); load() }
  catch (e: any) { alert(e?.response?.data?.detail || 'Failed to delete role') }
}

onMounted(load)
</script>

<style scoped>
.fp-field { display: flex; flex-direction: column; gap: 5px; margin-bottom: 12px; }
.fp-label { font-size: 12px; color: var(--text2); font-weight: 500; }
.fp-input { padding: 7px 10px; background: var(--bg3); border: 1px solid var(--border); border-radius: 5px; color: var(--text); font-size: 13px; outline: none; }
.fp-input:disabled { opacity: 0.6; }
.fp-error { font-size: 12px; color: var(--danger); padding: 6px 0; }
.fp-section-head { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text2); margin: 14px 0 8px; }
.opt-row { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text); padding: 5px 0; cursor: pointer; }
.opt-row input { accent-color: var(--accent2); width: 15px; height: 15px; }
.area-note { font-size: 11px; color: var(--text2); margin: 0 0 8px; line-height: 1.5; }
.cap-hint { font-style: normal; font-size: 10px; color: var(--text2); margin-left: 6px; }
.area-grid { border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.area-head, .area-row { display: grid; grid-template-columns: 1fr 52px 52px 52px 52px; align-items: center; }
.area-head { background: var(--bg3); font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text2); padding: 6px 12px; }
.area-head span:not(:first-child), .area-radio { text-align: center; }
.area-row { padding: 5px 12px; border-top: 1px solid var(--border); }
.area-name { font-size: 12.5px; color: var(--text); }
.area-radio { display: flex; justify-content: center; }
.area-radio input { accent-color: var(--accent2); cursor: pointer; }
</style>
