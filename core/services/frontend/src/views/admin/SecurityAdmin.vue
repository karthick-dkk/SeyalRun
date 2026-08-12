<template>
  <div>
    <!-- ── Command Groups ──────────────────────────────────────────────── -->
    <div class="card" style="margin-bottom:20px">
      <div class="card-header">
        Command Groups
        <button class="btn btn-primary btn-sm" @click="openCreateGroup">+ Command Group</button>
      </div>
      <table class="table">
        <thead>
          <tr><th>Name</th><th>Match Type</th><th>Patterns</th><th>Description</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="g in commandGroups" :key="g.id">
            <td style="font-weight:600">{{ g.name }}</td>
            <td><span class="badge badge-blue">{{ g.match_type }}</span></td>
            <td style="font-size:12px;color:var(--text2)">{{ (g.patterns || []).join(', ') || '—' }}</td>
            <td style="color:var(--text2)">{{ g.description || '—' }}</td>
            <td>
              <div style="display:flex;gap:8px;justify-content:flex-end">
                <button class="btn-pill btn-pill-outline" @click="openEditGroup(g)">✎ Edit</button>
                <button class="btn-pill btn-pill-outline" style="color:var(--danger);border-color:var(--danger)" @click="removeGroup(g)">🗑</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!commandGroups.length && !loadingGroups" style="padding:24px;text-align:center;color:var(--text2)">No command groups yet.</div>
      <div v-if="loadingGroups" style="padding:24px;text-align:center;color:var(--text2)">Loading…</div>
    </div>

    <!-- ── Command Filters ─────────────────────────────────────────────── -->
    <div class="card" style="margin-bottom:20px">
      <div class="card-header">
        Command Filters
        <button class="btn btn-primary btn-sm" @click="openCreateFilter">+ Command Filter</button>
      </div>
      <div style="padding:10px 16px;font-size:12px;color:var(--text2);border-bottom:1px solid var(--border)">
        Enforced live by the terminal service on every command typed in an SSH session.
        Principal/Target "Any" means the rule applies to every user and every host.
      </div>
      <table class="table">
        <thead>
          <tr><th>Name</th><th>Command Group</th><th>Principal</th><th>Target</th><th>Action</th><th>Priority</th><th>Status</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="f in commandFilters" :key="f.id">
            <td style="font-weight:600">{{ f.name }}</td>
            <td>{{ commandGroupName(f.command_group_id) }}</td>
            <td>
              <span v-if="f.user_id" class="badge badge-blue">👤 {{ userName(f.user_id) }}</span>
              <span v-else-if="f.user_group_id" class="badge badge-blue">👥 {{ userGroupName(f.user_group_id) }}</span>
              <span v-else style="color:var(--text2)">Any</span>
            </td>
            <td>
              <span v-if="f.host_id" class="badge badge-gray">🖥 {{ hostName(f.host_id) }}</span>
              <span v-else-if="f.host_group_id" class="badge badge-gray">📂 {{ hostGroupName(f.host_group_id) }}</span>
              <span v-else style="color:var(--text2)">Any</span>
            </td>
            <td>
              <span :class="['badge', actionBadgeClass(f.action)]">{{ f.action }}</span>
            </td>
            <td>{{ f.priority }}</td>
            <td>
              <span v-if="f.enabled" class="badge badge-green">Enabled</span>
              <span v-else class="badge badge-gray">Disabled</span>
            </td>
            <td>
              <div style="display:flex;gap:8px;justify-content:flex-end">
                <button class="btn-pill btn-pill-outline" @click="openEditFilter(f)">✎ Edit</button>
                <button class="btn-pill btn-pill-outline" style="color:var(--danger);border-color:var(--danger)" @click="removeFilter(f)">🗑</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!commandFilters.length && !loadingFilters" style="padding:24px;text-align:center;color:var(--text2)">No command filters yet.</div>
      <div v-if="loadingFilters" style="padding:24px;text-align:center;color:var(--text2)">Loading…</div>
    </div>

    <!-- ── Login ACLs ──────────────────────────────────────────────────── -->
    <div class="card" style="margin-bottom:20px">
      <div class="card-header">
        Login ACLs
        <button class="btn btn-primary btn-sm" @click="openCreateAcl">+ Login ACL</button>
      </div>
      <div style="padding:10px 16px;font-size:12px;color:var(--text2);border-bottom:1px solid var(--border)">
        Enforced live by the terminal service when an SSH session is requested.
      </div>
      <table class="table">
        <thead>
          <tr><th>Name</th><th>Principal</th><th>IP CIDR</th><th>Time Window</th><th>Days</th><th>Action</th><th>Priority</th><th>Status</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="a in loginAcls" :key="a.id">
            <td style="font-weight:600">{{ a.name }}</td>
            <td>
              <span v-if="a.user_id" class="badge badge-blue">👤 {{ userName(a.user_id) }}</span>
              <span v-else-if="a.user_group_id" class="badge badge-blue">👥 {{ userGroupName(a.user_group_id) }}</span>
              <span v-else style="color:var(--text2)">Any</span>
            </td>
            <td style="font-size:12px;color:var(--text2)">{{ a.ip_cidr || 'Any' }}</td>
            <td style="font-size:12px;color:var(--text2)">
              <span v-if="a.time_start || a.time_end">{{ a.time_start || '00:00' }} – {{ a.time_end || '23:59' }}</span>
              <span v-else>Any</span>
            </td>
            <td style="font-size:12px;color:var(--text2)">{{ daysLabel(a.days_of_week) }}</td>
            <td><span :class="['badge', actionBadgeClass(a.action)]">{{ a.action }}</span></td>
            <td>{{ a.priority }}</td>
            <td>
              <span v-if="a.enabled" class="badge badge-green">Enabled</span>
              <span v-else class="badge badge-gray">Disabled</span>
            </td>
            <td>
              <div style="display:flex;gap:8px;justify-content:flex-end">
                <button class="btn-pill btn-pill-outline" @click="openEditAcl(a)">✎ Edit</button>
                <button class="btn-pill btn-pill-outline" style="color:var(--danger);border-color:var(--danger)" @click="removeAcl(a)">🗑</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!loginAcls.length && !loadingAcls" style="padding:24px;text-align:center;color:var(--text2)">No login ACLs yet.</div>
      <div v-if="loadingAcls" style="padding:24px;text-align:center;color:var(--text2)">Loading…</div>
    </div>

    <!-- ── Personal Access Tokens ──────────────────────────────────────── -->
    <div class="card">
      <div class="card-header">
        Personal Access Tokens
        <button class="btn btn-primary btn-sm" @click="openCreateToken">+ Token</button>
      </div>
      <table class="table">
        <thead>
          <tr><th>Name</th><th>Token</th><th>Scopes</th><th>Expires</th><th>Last Used</th><th>Created</th><th>Status</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="t in apiTokens" :key="t.id">
            <td style="font-weight:600">{{ t.name }}</td>
            <td><span class="ip-mono">{{ t.token_prefix }}…</span></td>
            <td style="font-size:12px">{{ (t.scopes || []).join(', ') }}</td>
            <td style="font-size:12px;color:var(--text2)">{{ t.expires_at ? formatDate(t.expires_at) : 'Never' }}</td>
            <td style="font-size:12px;color:var(--text2)">{{ t.last_used_at ? formatDate(t.last_used_at) : 'Never' }}</td>
            <td style="font-size:12px;color:var(--text2)">{{ formatDate(t.created_at) }}</td>
            <td>
              <span v-if="t.revoked_at" class="badge badge-gray">Revoked</span>
              <span v-else class="badge badge-green">Active</span>
            </td>
            <td>
              <div style="display:flex;justify-content:flex-end">
                <button v-if="!t.revoked_at" class="btn-pill btn-pill-outline" style="color:var(--danger);border-color:var(--danger)" @click="revokeToken(t)">🗑 Revoke</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!apiTokens.length && !loadingTokens" style="padding:24px;text-align:center;color:var(--text2)">No personal access tokens yet.</div>
      <div v-if="loadingTokens" style="padding:24px;text-align:center;color:var(--text2)">Loading…</div>
    </div>

    <!-- ═══ Modals ═══════════════════════════════════════════════════════ -->

    <!-- Create / Edit Command Group -->
    <div v-if="showGroupModal" class="modal-overlay" @click.self="closeGroupModal">
      <div class="modal">
        <div class="modal-header">{{ editingGroup ? `Edit Command Group — ${editingGroup.name}` : 'Add Command Group' }}<button class="btn btn-sm btn-icon" @click="closeGroupModal">✕</button></div>
        <div class="modal-body">
          <div class="form-group"><label class="form-label">Name</label><input v-model="groupForm.name" class="input" placeholder="e.g. dangerous-commands" /></div>
          <div class="form-group"><label class="form-label">Description</label><input v-model="groupForm.description" class="input" placeholder="Optional description" /></div>
          <div class="form-group">
            <label class="form-label">Match Type</label>
            <select v-model="groupForm.match_type" class="input">
              <option value="regex">regex</option>
              <option value="exact">exact</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">Patterns (one per line)</label>
            <textarea v-model="groupForm.patternsText" class="input" rows="5" placeholder="rm -rf /*&#10;:(){ :|:& };:&#10;dd if=/dev/zero"></textarea>
          </div>
          <div v-if="groupError" style="color:var(--danger);font-size:12px">{{ groupError }}</div>
        </div>
        <div class="modal-footer">
          <button class="btn" @click="closeGroupModal">Cancel</button>
          <button class="btn btn-primary" @click="saveGroup" :disabled="savingGroup">{{ savingGroup ? 'Saving…' : (editingGroup ? 'Save' : 'Create') }}</button>
        </div>
      </div>
    </div>

    <!-- Create / Edit Command Filter -->
    <div v-if="showFilterModal" class="modal-overlay" @click.self="closeFilterModal">
      <div class="modal">
        <div class="modal-header">{{ editingFilter ? `Edit Command Filter — ${editingFilter.name}` : 'Add Command Filter' }}<button class="btn btn-sm btn-icon" @click="closeFilterModal">✕</button></div>
        <div class="modal-body">
          <div class="form-group"><label class="form-label">Name</label><input v-model="filterForm.name" class="input" placeholder="e.g. block-dangerous-on-prod" /></div>

          <div class="form-group">
            <label class="form-label">Command Group</label>
            <select v-model="filterForm.command_group_id" class="input">
              <option value="" disabled>— Select Command Group —</option>
              <option v-for="g in commandGroups" :key="g.id" :value="g.id">{{ g.name }}</option>
            </select>
          </div>

          <div class="form-group">
            <label class="form-label">Principal</label>
            <select v-model="filterPrincipalType" class="input" style="margin-bottom:8px">
              <option value="any">Any</option>
              <option value="user">User</option>
              <option value="user_group">User Group</option>
            </select>
            <AsyncPicker v-if="filterPrincipalType === 'user'" v-model="filterPrincipalPicker" :search-fn="searchUsers" :multiple="false" placeholder="Search users…" />
            <AsyncPicker v-else-if="filterPrincipalType === 'user_group'" v-model="filterPrincipalPicker" :search-fn="searchUserGroups" :multiple="false" placeholder="Search user groups…" />
          </div>

          <div class="form-group">
            <label class="form-label">Target</label>
            <select v-model="filterTargetType" class="input" style="margin-bottom:8px">
              <option value="any">Any</option>
              <option value="host">Host</option>
              <option value="host_group">Host Group</option>
            </select>
            <AsyncPicker v-if="filterTargetType === 'host'" v-model="filterTargetPicker" :search-fn="searchHosts" :multiple="false" placeholder="Search hosts…" />
            <AsyncPicker v-else-if="filterTargetType === 'host_group'" v-model="filterTargetPicker" :search-fn="searchHostGroups" :multiple="false" placeholder="Search host groups…" />
          </div>

          <div style="display:flex;gap:12px">
            <div class="form-group" style="flex:1">
              <label class="form-label">Action</label>
              <select v-model="filterForm.action" class="input">
                <option value="allow">allow</option>
                <option value="deny">deny</option>
                <option value="confirm">confirm</option>
              </select>
            </div>
            <div class="form-group" style="flex:1">
              <label class="form-label">Priority</label>
              <input v-model.number="filterForm.priority" type="number" class="input" />
            </div>
          </div>

          <div class="form-group">
            <label class="form-label">Status</label>
            <select v-model="filterForm.enabled" class="input">
              <option :value="true">Enabled</option>
              <option :value="false">Disabled</option>
            </select>
          </div>

          <div v-if="filterError" style="color:var(--danger);font-size:12px">{{ filterError }}</div>
        </div>
        <div class="modal-footer">
          <button class="btn" @click="closeFilterModal">Cancel</button>
          <button class="btn btn-primary" @click="saveFilter" :disabled="savingFilter">{{ savingFilter ? 'Saving…' : (editingFilter ? 'Save' : 'Create') }}</button>
        </div>
      </div>
    </div>

    <!-- Create / Edit Login ACL -->
    <div v-if="showAclModal" class="modal-overlay" @click.self="closeAclModal">
      <div class="modal">
        <div class="modal-header">{{ editingAcl ? `Edit Login ACL — ${editingAcl.name}` : 'Add Login ACL' }}<button class="btn btn-sm btn-icon" @click="closeAclModal">✕</button></div>
        <div class="modal-body">
          <div class="form-group"><label class="form-label">Name</label><input v-model="aclForm.name" class="input" placeholder="e.g. office-hours-only" /></div>

          <div class="form-group">
            <label class="form-label">Principal</label>
            <select v-model="aclPrincipalType" class="input" style="margin-bottom:8px">
              <option value="any">Any</option>
              <option value="user">User</option>
              <option value="user_group">User Group</option>
            </select>
            <AsyncPicker v-if="aclPrincipalType === 'user'" v-model="aclPrincipalPicker" :search-fn="searchUsers" :multiple="false" placeholder="Search users…" />
            <AsyncPicker v-else-if="aclPrincipalType === 'user_group'" v-model="aclPrincipalPicker" :search-fn="searchUserGroups" :multiple="false" placeholder="Search user groups…" />
          </div>

          <div class="form-group"><label class="form-label">IP CIDR (optional)</label><input v-model="aclForm.ip_cidr" class="input" placeholder="e.g. 10.0.0.0/24" /></div>

          <div style="display:flex;gap:12px">
            <div class="form-group" style="flex:1">
              <label class="form-label">Time Start (optional)</label>
              <input v-model="aclForm.time_start" type="time" class="input" />
            </div>
            <div class="form-group" style="flex:1">
              <label class="form-label">Time End (optional)</label>
              <input v-model="aclForm.time_end" type="time" class="input" />
            </div>
          </div>

          <div class="form-group">
            <label class="form-label">Days of Week</label>
            <div style="display:flex;gap:12px;flex-wrap:wrap">
              <label v-for="d in dayOptions" :key="d.value" style="display:flex;align-items:center;gap:6px;font-size:13px;color:var(--text2)">
                <input type="checkbox" :value="d.value" v-model="aclForm.days_of_week" /> {{ d.label }}
              </label>
            </div>
          </div>

          <div style="display:flex;gap:12px">
            <div class="form-group" style="flex:1">
              <label class="form-label">Action</label>
              <select v-model="aclForm.action" class="input">
                <option value="allow">allow</option>
                <option value="deny">deny</option>
              </select>
            </div>
            <div class="form-group" style="flex:1">
              <label class="form-label">Priority</label>
              <input v-model.number="aclForm.priority" type="number" class="input" />
            </div>
          </div>

          <div class="form-group">
            <label class="form-label">Status</label>
            <select v-model="aclForm.enabled" class="input">
              <option :value="true">Enabled</option>
              <option :value="false">Disabled</option>
            </select>
          </div>

          <div v-if="aclError" style="color:var(--danger);font-size:12px">{{ aclError }}</div>
        </div>
        <div class="modal-footer">
          <button class="btn" @click="closeAclModal">Cancel</button>
          <button class="btn btn-primary" @click="saveAcl" :disabled="savingAcl">{{ savingAcl ? 'Saving…' : (editingAcl ? 'Save' : 'Create') }}</button>
        </div>
      </div>
    </div>

    <!-- Create Token Modal -->
    <div v-if="showTokenModal" class="modal-overlay" @click.self="closeTokenModal">
      <div class="modal">
        <div class="modal-header">Create Personal Access Token<button class="btn btn-sm btn-icon" @click="closeTokenModal">✕</button></div>
        <div class="modal-body">
          <div class="form-group"><label class="form-label">Name</label><input v-model="tokenForm.name" class="input" placeholder="e.g. ci-pipeline" /></div>
          <div class="form-group">
            <label class="form-label">Scopes</label>
            <div style="display:flex;gap:14px;flex-wrap:wrap">
              <label v-for="s in scopeOptions" :key="s" style="display:flex;align-items:center;gap:6px;font-size:13px;color:var(--text2)">
                <input type="checkbox" :value="s" v-model="tokenForm.scopes" /> {{ s }}
              </label>
            </div>
          </div>
          <div class="form-group"><label class="form-label">Expires (optional)</label><input v-model="tokenForm.expires_at" type="datetime-local" class="input" /></div>
          <div v-if="tokenError" style="color:var(--danger);font-size:12px">{{ tokenError }}</div>
        </div>
        <div class="modal-footer">
          <button class="btn" @click="closeTokenModal">Cancel</button>
          <button class="btn btn-primary" @click="createToken" :disabled="savingToken">{{ savingToken ? 'Creating…' : 'Create' }}</button>
        </div>
      </div>
    </div>

    <!-- Token Created (shown once) Modal -->
    <div v-if="createdToken" class="modal-overlay">
      <div class="modal">
        <div class="modal-header">Token Created</div>
        <div class="modal-body">
          <div style="color:var(--warn);font-size:13px;margin-bottom:12px">
            Copy this token now — it will not be shown again.
          </div>
          <div class="form-group">
            <label class="form-label">Token</label>
            <input :value="createdToken.token" readonly class="input ip-mono" @click="($event.target as HTMLInputElement).select()" />
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-primary" @click="createdToken = null">Done</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import AsyncPicker, { type PickerItem } from '@/components/common/AsyncPicker.vue'
import api from '@/api/client'
import { useConfirm } from '@/composables/useConfirm'

const { confirm } = useConfirm()

const dayOptions = [
  { value: 0, label: 'Sun' }, { value: 1, label: 'Mon' }, { value: 2, label: 'Tue' },
  { value: 3, label: 'Wed' }, { value: 4, label: 'Thu' }, { value: 5, label: 'Fri' }, { value: 6, label: 'Sat' },
]
const scopeOptions = ['read', 'metrics:read', 'automation:run', 'admin']

const commandGroups = ref<any[]>([])
const commandFilters = ref<any[]>([])
const loginAcls = ref<any[]>([])
const apiTokens = ref<any[]>([])
const users = ref<any[]>([])
const userGroups = ref<any[]>([])
const hosts = ref<any[]>([])
const hostGroups = ref<any[]>([])

const loadingGroups = ref(false)
const loadingFilters = ref(false)
const loadingAcls = ref(false)
const loadingTokens = ref(false)

const userById = computed(() => new Map(users.value.map(u => [u.id, u])))
const userGroupById = computed(() => new Map(userGroups.value.map(g => [g.id, g])))
const hostById = computed(() => new Map(hosts.value.map(h => [h.id, h])))
const hostGroupById = computed(() => new Map(hostGroups.value.map(g => [g.id, g])))
const commandGroupById = computed(() => new Map(commandGroups.value.map(g => [g.id, g])))

function userName(id: string) { return userById.value.get(id)?.username ?? id }
function userGroupName(id: string) { return userGroupById.value.get(id)?.name ?? id }
function hostName(id: string) { return hostById.value.get(id)?.name ?? id }
function hostGroupName(id: string) { return hostGroupById.value.get(id)?.name ?? id }
function commandGroupName(id: string) { return commandGroupById.value.get(id)?.name ?? id }
function formatDate(d: string) { return new Date(d).toLocaleString() }
function daysLabel(days: number[]) {
  if (!days || days.length === 7) return 'Every day'
  return days.map(d => dayOptions.find(o => o.value === d)?.label ?? d).join(', ')
}
function actionBadgeClass(action: string) {
  if (action === 'allow') return 'badge-green'
  if (action === 'deny') return 'badge-red'
  return 'badge-gray'
}

// ── Data loading ─────────────────────────────────────────────────────────
async function loadCommandGroups() {
  loadingGroups.value = true
  try {
    const { data } = await api.get('/command-groups')
    commandGroups.value = data
  } finally {
    loadingGroups.value = false
  }
}
async function loadCommandFilters() {
  loadingFilters.value = true
  try {
    const { data } = await api.get('/command-filters')
    commandFilters.value = data
  } finally {
    loadingFilters.value = false
  }
}
async function loadLoginAcls() {
  loadingAcls.value = true
  try {
    const { data } = await api.get('/login-acls')
    loginAcls.value = data
  } finally {
    loadingAcls.value = false
  }
}
async function loadApiTokens() {
  loadingTokens.value = true
  try {
    const { data } = await api.get('/api-tokens')
    apiTokens.value = data
  } finally {
    loadingTokens.value = false
  }
}
async function loadReferenceData() {
  const [u, ug, h, hg] = await Promise.all([
    api.get('/users'),
    api.get('/users/groups'),
    api.get('/hosts'),
    api.get('/host-groups'),
  ])
  users.value = u.data
  userGroups.value = ug.data
  hosts.value = h.data
  hostGroups.value = hg.data
}

// ── Command Groups ───────────────────────────────────────────────────────
const showGroupModal = ref(false)
const editingGroup = ref<any>(null)
const groupForm = reactive({ name: '', description: '', match_type: 'regex', patternsText: '' })
const groupError = ref('')
const savingGroup = ref(false)

function openCreateGroup() {
  editingGroup.value = null
  Object.assign(groupForm, { name: '', description: '', match_type: 'regex', patternsText: '' })
  groupError.value = ''
  showGroupModal.value = true
}
function openEditGroup(g: any) {
  editingGroup.value = g
  Object.assign(groupForm, { name: g.name, description: g.description, match_type: g.match_type, patternsText: (g.patterns || []).join('\n') })
  groupError.value = ''
  showGroupModal.value = true
}
function closeGroupModal() {
  showGroupModal.value = false
  editingGroup.value = null
}
async function saveGroup() {
  savingGroup.value = true
  groupError.value = ''
  try {
    const payload = {
      name: groupForm.name,
      description: groupForm.description,
      match_type: groupForm.match_type,
      patterns: groupForm.patternsText.split('\n').map(p => p.trim()).filter(Boolean),
    }
    if (editingGroup.value) {
      await api.put(`/command-groups/${editingGroup.value.id}`, payload)
    } else {
      await api.post('/command-groups', payload)
    }
    closeGroupModal()
    loadCommandGroups()
  } catch (e: any) {
    groupError.value = e?.response?.data?.detail || 'Failed to save command group'
  } finally {
    savingGroup.value = false
  }
}
async function removeGroup(g: any) {
  if (!await confirm(`Delete command group "${g.name}"?`, { title: 'Delete Command Group', danger: true, confirmLabel: 'Delete' })) return
  try {
    await api.delete(`/command-groups/${g.id}`)
    loadCommandGroups()
  } catch (e: any) {
    alert(e?.response?.data?.detail || 'Failed to delete command group')
  }
}

// ── Command Filters ──────────────────────────────────────────────────────
const showFilterModal = ref(false)
const editingFilter = ref<any>(null)
const filterPrincipalType = ref<'any' | 'user' | 'user_group'>('any')
const filterTargetType = ref<'any' | 'host' | 'host_group'>('any')
const filterPrincipalPicker = ref<PickerItem[]>([])
const filterTargetPicker = ref<PickerItem[]>([])
const filterForm = reactive({ name: '', command_group_id: '', action: 'deny', priority: 50, enabled: true })
const filterError = ref('')
const savingFilter = ref(false)

function openCreateFilter() {
  editingFilter.value = null
  filterPrincipalType.value = 'any'
  filterTargetType.value = 'any'
  filterPrincipalPicker.value = []
  filterTargetPicker.value = []
  Object.assign(filterForm, { name: '', command_group_id: commandGroups.value[0]?.id || '', action: 'deny', priority: 50, enabled: true })
  filterError.value = ''
  showFilterModal.value = true
}
function openEditFilter(f: any) {
  editingFilter.value = f
  if (f.user_id) {
    filterPrincipalType.value = 'user'
    filterPrincipalPicker.value = [{ id: f.user_id, label: userName(f.user_id) }]
  } else if (f.user_group_id) {
    filterPrincipalType.value = 'user_group'
    filterPrincipalPicker.value = [{ id: f.user_group_id, label: userGroupName(f.user_group_id) }]
  } else {
    filterPrincipalType.value = 'any'
    filterPrincipalPicker.value = []
  }
  if (f.host_id) {
    filterTargetType.value = 'host'
    filterTargetPicker.value = [{ id: f.host_id, label: hostName(f.host_id) }]
  } else if (f.host_group_id) {
    filterTargetType.value = 'host_group'
    filterTargetPicker.value = [{ id: f.host_group_id, label: hostGroupName(f.host_group_id) }]
  } else {
    filterTargetType.value = 'any'
    filterTargetPicker.value = []
  }
  Object.assign(filterForm, { name: f.name, command_group_id: f.command_group_id, action: f.action, priority: f.priority, enabled: f.enabled })
  filterError.value = ''
  showFilterModal.value = true
}
function closeFilterModal() {
  showFilterModal.value = false
  editingFilter.value = null
}
async function saveFilter() {
  if (!filterForm.command_group_id) { filterError.value = 'Select a command group'; return }
  savingFilter.value = true
  filterError.value = ''
  try {
    const principal = filterPrincipalPicker.value[0]
    const target = filterTargetPicker.value[0]
    const payload = {
      name: filterForm.name,
      command_group_id: filterForm.command_group_id,
      user_id: filterPrincipalType.value === 'user' ? principal?.id || null : null,
      user_group_id: filterPrincipalType.value === 'user_group' ? principal?.id || null : null,
      host_id: filterTargetType.value === 'host' ? target?.id || null : null,
      host_group_id: filterTargetType.value === 'host_group' ? target?.id || null : null,
      action: filterForm.action,
      priority: filterForm.priority,
      enabled: filterForm.enabled,
    }
    if (editingFilter.value) {
      await api.put(`/command-filters/${editingFilter.value.id}`, payload)
    } else {
      await api.post('/command-filters', payload)
    }
    closeFilterModal()
    loadCommandFilters()
  } catch (e: any) {
    filterError.value = e?.response?.data?.detail || 'Failed to save command filter'
  } finally {
    savingFilter.value = false
  }
}
async function removeFilter(f: any) {
  if (!await confirm(`Delete command filter "${f.name}"?`, { title: 'Delete Command Filter', danger: true, confirmLabel: 'Delete' })) return
  try {
    await api.delete(`/command-filters/${f.id}`)
    loadCommandFilters()
  } catch (e: any) {
    alert(e?.response?.data?.detail || 'Failed to delete command filter')
  }
}

// ── Login ACLs ───────────────────────────────────────────────────────────
const showAclModal = ref(false)
const editingAcl = ref<any>(null)
const aclPrincipalType = ref<'any' | 'user' | 'user_group'>('any')
const aclPrincipalPicker = ref<PickerItem[]>([])
const aclForm = reactive({
  name: '', ip_cidr: '', time_start: '', time_end: '',
  days_of_week: [0, 1, 2, 3, 4, 5, 6] as number[], action: 'allow', priority: 50, enabled: true,
})
const aclError = ref('')
const savingAcl = ref(false)

function openCreateAcl() {
  editingAcl.value = null
  aclPrincipalType.value = 'any'
  aclPrincipalPicker.value = []
  Object.assign(aclForm, { name: '', ip_cidr: '', time_start: '', time_end: '', days_of_week: [0, 1, 2, 3, 4, 5, 6], action: 'allow', priority: 50, enabled: true })
  aclError.value = ''
  showAclModal.value = true
}
function openEditAcl(a: any) {
  editingAcl.value = a
  if (a.user_id) {
    aclPrincipalType.value = 'user'
    aclPrincipalPicker.value = [{ id: a.user_id, label: userName(a.user_id) }]
  } else if (a.user_group_id) {
    aclPrincipalType.value = 'user_group'
    aclPrincipalPicker.value = [{ id: a.user_group_id, label: userGroupName(a.user_group_id) }]
  } else {
    aclPrincipalType.value = 'any'
    aclPrincipalPicker.value = []
  }
  Object.assign(aclForm, {
    name: a.name, ip_cidr: a.ip_cidr || '', time_start: a.time_start || '', time_end: a.time_end || '',
    days_of_week: [...(a.days_of_week || [])], action: a.action, priority: a.priority, enabled: a.enabled,
  })
  aclError.value = ''
  showAclModal.value = true
}
function closeAclModal() {
  showAclModal.value = false
  editingAcl.value = null
}
async function saveAcl() {
  savingAcl.value = true
  aclError.value = ''
  try {
    const principal = aclPrincipalPicker.value[0]
    const payload = {
      name: aclForm.name,
      user_id: aclPrincipalType.value === 'user' ? principal?.id || null : null,
      user_group_id: aclPrincipalType.value === 'user_group' ? principal?.id || null : null,
      ip_cidr: aclForm.ip_cidr || null,
      time_start: aclForm.time_start || null,
      time_end: aclForm.time_end || null,
      days_of_week: aclForm.days_of_week,
      action: aclForm.action,
      priority: aclForm.priority,
      enabled: aclForm.enabled,
    }
    if (editingAcl.value) {
      await api.put(`/login-acls/${editingAcl.value.id}`, payload)
    } else {
      await api.post('/login-acls', payload)
    }
    closeAclModal()
    loadLoginAcls()
  } catch (e: any) {
    aclError.value = e?.response?.data?.detail || 'Failed to save login ACL'
  } finally {
    savingAcl.value = false
  }
}
async function removeAcl(a: any) {
  if (!await confirm(`Delete login ACL "${a.name}"?`, { title: 'Delete Login ACL', danger: true, confirmLabel: 'Delete' })) return
  try {
    await api.delete(`/login-acls/${a.id}`)
    loadLoginAcls()
  } catch (e: any) {
    alert(e?.response?.data?.detail || 'Failed to delete login ACL')
  }
}

// ── Personal Access Tokens ───────────────────────────────────────────────
const showTokenModal = ref(false)
const tokenForm = reactive({ name: '', scopes: ['read'] as string[], expires_at: '' })
const tokenError = ref('')
const savingToken = ref(false)
const createdToken = ref<any>(null)

function openCreateToken() {
  Object.assign(tokenForm, { name: '', scopes: ['read'], expires_at: '' })
  tokenError.value = ''
  showTokenModal.value = true
}
function closeTokenModal() { showTokenModal.value = false }

async function createToken() {
  savingToken.value = true
  tokenError.value = ''
  try {
    const payload = {
      name: tokenForm.name,
      scopes: tokenForm.scopes,
      expires_at: tokenForm.expires_at ? new Date(tokenForm.expires_at).toISOString() : null,
    }
    const { data } = await api.post('/api-tokens', payload)
    closeTokenModal()
    createdToken.value = data
    loadApiTokens()
  } catch (e: any) {
    tokenError.value = e?.response?.data?.detail || 'Failed to create token'
  } finally {
    savingToken.value = false
  }
}
async function revokeToken(t: any) {
  if (!await confirm(`Revoke token "${t.name}"? This cannot be undone.`, { title: 'Revoke Token', danger: true, confirmLabel: 'Revoke' })) return
  try {
    await api.delete(`/api-tokens/${t.id}`)
    loadApiTokens()
  } catch (e: any) {
    alert(e?.response?.data?.detail || 'Failed to revoke token')
  }
}

// ── Search functions ─────────────────────────────────────────────────────
async function searchUsers(query: string): Promise<PickerItem[]> {
  const q = query.trim().toLowerCase()
  return users.value
    .filter(u => !q || u.username.toLowerCase().includes(q) || u.display_name?.toLowerCase().includes(q))
    .slice(0, 20)
    .map(u => ({ id: u.id, label: u.username, sublabel: u.display_name }))
}
async function searchUserGroups(query: string): Promise<PickerItem[]> {
  const q = query.trim().toLowerCase()
  return userGroups.value
    .filter(g => !q || g.name.toLowerCase().includes(q))
    .slice(0, 20)
    .map(g => ({ id: g.id, label: g.name, sublabel: g.description }))
}
async function searchHosts(query: string): Promise<PickerItem[]> {
  const q = query.trim().toLowerCase()
  return hosts.value
    .filter(h => !q || h.name.toLowerCase().includes(q) || h.ip.includes(q))
    .slice(0, 20)
    .map(h => ({ id: h.id, label: h.name, sublabel: h.ip }))
}
async function searchHostGroups(query: string): Promise<PickerItem[]> {
  const q = query.trim().toLowerCase()
  return hostGroups.value
    .filter(g => !q || g.name.toLowerCase().includes(q))
    .slice(0, 20)
    .map(g => ({ id: g.id, label: g.name, sublabel: g.description }))
}

onMounted(() => {
  loadReferenceData()
  loadCommandGroups()
  loadCommandFilters()
  loadLoginAcls()
  loadApiTokens()
})
</script>
