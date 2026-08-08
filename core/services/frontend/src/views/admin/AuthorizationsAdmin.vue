<template>
  <div>
    <div class="card">
      <div class="card-header">
        Authorizations
        <div style="display:flex;align-items:center;gap:10px">
          <span v-if="selectedIds.size" class="bulk-sel-badge">{{ selectedIds.size }} selected</span>
          <button v-if="selectedIds.size" class="btn btn-sm" style="color:var(--danger);border-color:var(--danger)" @click="bulkDelete">Delete Selected</button>
          <button class="btn btn-primary btn-sm" @click="openCreate" :disabled="!!expandedId">+ Authorization</button>
        </div>
      </div>
      <table class="table">
        <thead>
          <tr>
            <th style="width:36px">
              <input type="checkbox" :checked="allSelected" :indeterminate.prop="someSelected && !allSelected" @change="toggleAll" style="accent-color:#58a6ff;cursor:pointer" />
            </th>
            <th>Name</th>
            <th>Principal</th>
            <th>Target</th>
            <th>Credential</th>
            <th>Actions</th>
            <th>Validity</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <!-- New-rule expand row at top -->
          <tr v-if="expandedId === '__new__'" class="expand-row">
            <td colspan="9" style="padding:0">
              <div class="expand-form" @keydown.esc.stop="closeExpand" @keydown.enter.exact.stop.prevent="save">
                <div class="expand-form-head">
                  <span>Add Authorization</span>
                  <button class="btn btn-sm btn-icon" @click="closeExpand">✕</button>
                </div>
                <div class="expand-body">
                  <div class="eg-row">
                    <div class="eg-field eg-field--wide">
                      <label class="eg-label">Rule Name <span class="eg-opt">(auto-filled)</span></label>
                      <input v-model="form.name" class="input" placeholder="e.g. dev-team → web-servers" ref="nameInputRef" />
                    </div>
                  </div>
                  <div class="eg-row eg-row--cols4">
                    <div class="eg-section">
                      <div class="eg-section-head">
                        <span>Principal</span>
                        <div class="eg-toggle-group">
                          <button :class="['eg-toggle', principalType==='user'&&'active']" @click.stop="principalType='user';form.principalIds=[]">Users</button>
                          <button :class="['eg-toggle', principalType==='user_group'&&'active']" @click.stop="principalType='user_group';form.principalIds=[]">Groups</button>
                        </div>
                      </div>
                      <div class="check-grid">
                        <label v-for="item in (principalType==='user' ? users : userGroups)" :key="item.id" class="check-item" :class="{'check-item--sel': form.principalIds.includes(item.id)}">
                          <input type="checkbox" :value="item.id" v-model="form.principalIds" />
                          <div class="check-item-body">
                            <span class="check-item-name">{{ principalType==='user' ? item.username : item.name }}</span>
                            <span class="check-item-sub">{{ principalType==='user' ? (item.display_name||'') : (item.description||'') }}</span>
                          </div>
                        </label>
                        <div v-if="!(principalType==='user' ? users : userGroups).length" class="check-empty">None found.</div>
                      </div>
                      <div class="check-sel-hint"><a class="check-all-link" @click.prevent="toggleAllPrincipals">{{ allPrincipalsSel ? 'Clear all' : 'Select all' }}</a><span v-if="form.principalIds.length"> · {{ form.principalIds.length }} selected</span></div>
                    </div>
                    <div class="eg-section">
                      <div class="eg-section-head">
                        <span>Target</span>
                        <div class="eg-toggle-group">
                          <button :class="['eg-toggle', targetType==='host'&&'active']" @click.stop="targetType='host';form.targetIds=[]">Hosts</button>
                          <button :class="['eg-toggle', targetType==='host_group'&&'active']" @click.stop="targetType='host_group';form.targetIds=[]">Groups</button>
                        </div>
                      </div>
                      <div class="check-grid">
                        <label v-for="item in (targetType==='host' ? hosts : hostGroups)" :key="item.id" class="check-item" :class="{'check-item--sel': form.targetIds.includes(item.id)}">
                          <input type="checkbox" :value="item.id" v-model="form.targetIds" />
                          <div class="check-item-body">
                            <span class="check-item-name">{{ item.name }}</span>
                            <span class="check-item-sub">{{ targetType==='host' ? item.ip : (item.description||'') }}</span>
                          </div>
                        </label>
                        <div v-if="!(targetType==='host' ? hosts : hostGroups).length" class="check-empty">None found.</div>
                      </div>
                      <div class="check-sel-hint"><a class="check-all-link" @click.prevent="toggleAllTargets">{{ allTargetsSel ? 'Clear all' : 'Select all' }}</a><span v-if="form.targetIds.length"> · {{ form.targetIds.length }} selected</span></div>
                    </div>
                    <div class="eg-section">
                      <div class="eg-section-head"><span>Credential</span></div>
                      <div class="check-grid">
                        <label v-for="c in credentials" :key="c.id" class="cred-pick" :class="{'cred-pick--sel': form.credentialIds.includes(c.id)}">
                          <input type="checkbox" :value="c.id" v-model="form.credentialIds" />
                          <span>{{ c.name || c.username }} <span class="cred-pick-meta">({{ c.secret_type }})</span></span>
                        </label>
                        <div v-if="!credentials.length" class="check-empty">None found.</div>
                      </div>
                      <div class="check-sel-hint"><a class="check-all-link" @click.prevent="toggleAllCreds">{{ allCredsSel ? 'Clear all' : 'Select all' }}</a><span v-if="form.credentialIds.length"> · {{ form.credentialIds.length }} selected</span><span v-else> · none = any</span></div>
                    </div>
                    <div class="eg-section">
                      <div class="eg-section-head"><span>Actions &amp; Validity</span></div>
                      <div style="padding:10px;display:flex;flex-direction:column;gap:12px">
                        <div class="eg-field">
                          <label class="eg-label">Allowed Actions</label>
                          <div class="act-check-row">
                            <label v-for="act in availableActions" :key="act" class="act-check">
                              <input type="checkbox" :value="act" v-model="form.actions" />{{ act }}
                            </label>
                          </div>
                        </div>
                        <div class="eg-field"><label class="eg-label">Valid From</label><input v-model="form.date_start" type="datetime-local" class="input" /></div>
                        <div class="eg-field"><label class="eg-label">Until <span class="eg-opt">(defaults if left blank)</span></label><input v-model="form.date_expired" type="datetime-local" class="input" /></div>
                      </div>
                    </div>
                  </div>
                  <div v-if="formError" class="eg-error">{{ formError }}</div>
                  <div class="eg-bulk-note">
                    <span class="bulk-dot"></span>
                    Grants require a second admin's approval before they take effect — this one will be <strong>Pending Approval</strong> until approved.
                  </div>
                  <div v-if="form.principalIds.length > 1 || form.targetIds.length > 1" class="eg-bulk-note">
                    <span class="bulk-dot"></span>
                    One rule — grants <strong>{{ form.principalIds.length }}</strong> {{ principalType==='user' ? 'user(s)' : 'group(s)' }}
                    access to <strong>{{ form.targetIds.length }}</strong> {{ targetType==='host' ? 'host(s)' : 'host group(s)' }}
                  </div>
                </div>
                <div class="expand-footer">
                  <span class="eg-hint">Enter to save · Esc to cancel</span>
                  <div style="display:flex;gap:8px">
                    <button class="btn" @click="closeExpand">Cancel</button>
                    <button class="btn btn-primary" @click="save" :disabled="saving">{{ saving ? 'Saving…' : 'Create' }}</button>
                  </div>
                </div>
              </div>
            </td>
          </tr>

          <!-- Existing authorizations -->
          <template v-for="a in authorizations" :key="a.id">
            <!-- Normal row -->
            <tr v-if="expandedId !== a.id" :class="{ 'row-active': expandedId === a.id, 'row-selected': selectedIds.has(a.id) }">
              <td>
                <input type="checkbox" :checked="selectedIds.has(a.id)" @change="toggleRow(a.id)" style="accent-color:#58a6ff;cursor:pointer" />
              </td>
              <td style="font-weight:600">{{ a.name }}</td>
              <td>
                <template v-if="principalsOf(a).type === 'user'">
                  <span v-for="id in principalsOf(a).ids" :key="id" class="badge badge-blue" style="margin:1px">👤 {{ userName(id) }}</span>
                </template>
                <template v-else-if="principalsOf(a).type === 'user_group'">
                  <span v-for="id in principalsOf(a).ids" :key="id" class="badge badge-blue" style="margin:1px">👥 {{ userGroupName(id) }}</span>
                </template>
                <span v-else style="color:var(--text2)">—</span>
              </td>
              <td>
                <template v-if="targetsOf(a).type === 'host'">
                  <span v-for="id in targetsOf(a).ids" :key="id" class="badge badge-gray" style="margin:1px">&#128187; {{ hostName(id) }}</span>
                </template>
                <template v-else-if="targetsOf(a).type === 'host_group'">
                  <span v-for="id in targetsOf(a).ids" :key="id" class="badge badge-gray" style="margin:1px">&#128193; {{ hostGroupName(id) }}</span>
                </template>
                <span v-else style="color:var(--text2)">—</span>
              </td>
              <td style="font-size:12px">
                <template v-if="credsOf(a).length">
                  <span v-for="id in credsOf(a)" :key="id" class="badge badge-gray" style="margin:1px">{{ credentialName(id) }}</span>
                </template>
                <span v-else style="color:var(--text2)">any</span>
              </td>
              <td style="font-size:12px">{{ (a.actions||[]).join(', ') }}</td>
              <td style="font-size:12px;color:var(--text2)">
                <div v-if="a.date_start">From {{ formatDate(a.date_start) }}</div>
                <div v-if="a.date_expired">Until {{ formatDate(a.date_expired) }}</div>
                <div v-if="!a.date_start && !a.date_expired">Always</div>
              </td>
              <td>
                <span v-if="a.status === 'active'" class="badge badge-green">Active</span>
                <span v-else-if="a.status === 'pending_approval'" class="badge badge-yellow">Pending Approval</span>
                <span v-else-if="a.status === 'rejected'" class="badge badge-gray">Rejected</span>
                <span v-else-if="a.status === 'expired'" class="badge badge-gray">Expired</span>
                <span v-else class="badge badge-gray">{{ a.status || (a.enabled ? 'Active' : 'Disabled') }}</span>
              </td>
              <td>
                <div style="display:flex;gap:8px;justify-content:flex-end">
                  <template v-if="a.status === 'pending_approval'">
                    <button
                      class="btn-pill btn-pill-outline" style="color:var(--accent2);border-color:var(--accent2)"
                      :disabled="!!expandedId || a.requested_by === auth.user?.id"
                      :title="a.requested_by === auth.user?.id ? 'You requested this — a different admin must approve it' : ''"
                      @click="approve(a)"
                    >✓ Approve</button>
                    <button class="btn-pill btn-pill-outline" style="color:var(--danger);border-color:var(--danger)" :disabled="!!expandedId" @click="reject(a)">✕ Reject</button>
                  </template>
                  <button class="btn-pill btn-pill-outline" :disabled="!!expandedId" @click="openEdit(a)">✎ Edit</button>
                  <button class="btn-pill btn-pill-outline" style="color:var(--danger);border-color:var(--danger)" :disabled="!!expandedId" @click="remove(a)">✕</button>
                </div>
              </td>
            </tr>

            <!-- Inline edit expand row -->
            <tr v-else class="expand-row">
              <td colspan="9" style="padding:0">
                <div class="expand-form" @keydown.esc.stop="closeExpand" @keydown.enter.exact.stop.prevent="save">
                  <div class="expand-form-head">
                    <span>Edit — {{ a.name }}</span>
                    <button class="btn btn-sm btn-icon" @click="closeExpand" title="Close (Esc)">✕</button>
                  </div>
                  <div class="expand-body">
                    <div class="eg-row">
                      <div class="eg-field eg-field--wide">
                        <label class="eg-label">Rule Name</label>
                        <input v-model="form.name" class="input" ref="nameInputRef" />
                      </div>
                    </div>
                    <div class="eg-row eg-row--cols4">
                      <div class="eg-section">
                        <div class="eg-section-head">
                          <span>Principal</span>
                          <div class="eg-toggle-group">
                            <button :class="['eg-toggle', principalType==='user'&&'active']" @click.stop="principalType='user';form.principalIds=[]">Users</button>
                            <button :class="['eg-toggle', principalType==='user_group'&&'active']" @click.stop="principalType='user_group';form.principalIds=[]">Groups</button>
                          </div>
                        </div>
                        <div class="check-grid">
                          <label v-for="item in (principalType==='user' ? users : userGroups)" :key="item.id" class="check-item" :class="{'check-item--sel': form.principalIds.includes(item.id)}">
                            <input type="checkbox" :value="item.id" v-model="form.principalIds" />
                            <div class="check-item-body">
                              <span class="check-item-name">{{ principalType==='user' ? item.username : item.name }}</span>
                              <span class="check-item-sub">{{ principalType==='user' ? (item.display_name||'') : (item.description||'') }}</span>
                            </div>
                          </label>
                          <div v-if="!(principalType==='user' ? users : userGroups).length" class="check-empty">None found.</div>
                        </div>
                        <div class="check-sel-hint"><a class="check-all-link" @click.prevent="toggleAllPrincipals">{{ allPrincipalsSel ? 'Clear all' : 'Select all' }}</a><span v-if="form.principalIds.length"> · {{ form.principalIds.length }} selected</span></div>
                      </div>
                      <div class="eg-section">
                        <div class="eg-section-head">
                          <span>Target</span>
                          <div class="eg-toggle-group">
                            <button :class="['eg-toggle', targetType==='host'&&'active']" @click.stop="targetType='host';form.targetIds=[]">Hosts</button>
                            <button :class="['eg-toggle', targetType==='host_group'&&'active']" @click.stop="targetType='host_group';form.targetIds=[]">Groups</button>
                          </div>
                        </div>
                        <div class="check-grid">
                          <label v-for="item in (targetType==='host' ? hosts : hostGroups)" :key="item.id" class="check-item" :class="{'check-item--sel': form.targetIds.includes(item.id)}">
                            <input type="checkbox" :value="item.id" v-model="form.targetIds" />
                            <div class="check-item-body">
                              <span class="check-item-name">{{ item.name }}</span>
                              <span class="check-item-sub">{{ targetType==='host' ? item.ip : (item.description||'') }}</span>
                            </div>
                          </label>
                          <div v-if="!(targetType==='host' ? hosts : hostGroups).length" class="check-empty">None found.</div>
                        </div>
                        <div class="check-sel-hint"><a class="check-all-link" @click.prevent="toggleAllTargets">{{ allTargetsSel ? 'Clear all' : 'Select all' }}</a><span v-if="form.targetIds.length"> · {{ form.targetIds.length }} selected</span></div>
                      </div>
                      <div class="eg-section">
                        <div class="eg-section-head"><span>Credential</span></div>
                        <div class="check-grid">
                          <label v-for="c in credentials" :key="c.id" class="cred-pick" :class="{'cred-pick--sel': form.credentialIds.includes(c.id)}">
                            <input type="checkbox" :value="c.id" v-model="form.credentialIds" />
                            <span>{{ c.name || c.username }} <span class="cred-pick-meta">({{ c.secret_type }})</span></span>
                          </label>
                          <div v-if="!credentials.length" class="check-empty">None found.</div>
                        </div>
                        <div class="check-sel-hint"><a class="check-all-link" @click.prevent="toggleAllCreds">{{ allCredsSel ? 'Clear all' : 'Select all' }}</a><span v-if="form.credentialIds.length"> · {{ form.credentialIds.length }} selected</span><span v-else> · none = any</span></div>
                      </div>
                      <div class="eg-section">
                        <div class="eg-section-head"><span>Actions &amp; Validity</span></div>
                        <div style="padding:10px;display:flex;flex-direction:column;gap:12px">
                          <div class="eg-field">
                            <label class="eg-label">Allowed Actions</label>
                            <div class="act-check-row">
                              <label v-for="act in availableActions" :key="act" class="act-check">
                                <input type="checkbox" :value="act" v-model="form.actions" />{{ act }}
                              </label>
                            </div>
                          </div>
                          <div class="eg-field"><label class="eg-label">Valid From</label><input v-model="form.date_start" type="datetime-local" class="input" /></div>
                          <div class="eg-field"><label class="eg-label">Until <span class="eg-opt">(defaults if left blank)</span></label><input v-model="form.date_expired" type="datetime-local" class="input" /></div>
                        </div>
                      </div>
                    </div>
                    <div v-if="formError" class="eg-error">{{ formError }}</div>
                    <div class="eg-bulk-note">
                      <span class="bulk-dot"></span>
                      Saving reopens this grant for approval — it goes back to <strong>Pending Approval</strong> until a different admin approves the change.
                    </div>
                  </div>
                  <div class="expand-footer">
                    <span class="eg-hint">Enter to save · Esc to cancel</span>
                    <div style="display:flex;gap:8px">
                      <button class="btn" @click="closeExpand">Cancel</button>
                      <button class="btn btn-primary" @click="save" :disabled="saving">{{ saving ? 'Saving…' : 'Save Changes' }}</button>
                    </div>
                  </div>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      <div v-if="!authorizations.length && !loading && expandedId !== '__new__'" style="padding:32px;text-align:center;color:var(--text2)">No authorizations yet.</div>
      <div v-if="loading" style="padding:32px;text-align:center;color:var(--text2)">Loading…</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import api from '@/api/client'
import { useConfirm } from '@/composables/useConfirm'
import { useAuthStore } from '@/stores/auth'

const { confirm } = useConfirm()
const auth = useAuthStore()
const availableActions = ['ssh', 'sftp', 'upload', 'download']

const authorizations = ref<any[]>([])
const users          = ref<any[]>([])
const userGroups     = ref<any[]>([])
const hosts          = ref<any[]>([])
const hostGroups     = ref<any[]>([])
const credentials    = ref<any[]>([])
const loading        = ref(false)

// ── Bulk select ───────────────────────────────────────────────────────────
const selectedIds = ref(new Set<string>())
const allSelected  = computed(() => authorizations.value.length > 0 && authorizations.value.every(a => selectedIds.value.has(a.id)))
const someSelected = computed(() => authorizations.value.some(a => selectedIds.value.has(a.id)))

function toggleRow(id: string) {
  const s = new Set(selectedIds.value)
  s.has(id) ? s.delete(id) : s.add(id)
  selectedIds.value = s
}

function toggleAll() {
  if (allSelected.value) {
    selectedIds.value = new Set()
  } else {
    selectedIds.value = new Set(authorizations.value.map(a => a.id))
  }
}

async function bulkDelete() {
  const ids = Array.from(selectedIds.value)
  if (!ids.length) return
  if (!await confirm(`Delete ${ids.length} authorization${ids.length > 1 ? 's' : ''}?`, { title: 'Bulk Delete', danger: true, confirmLabel: 'Delete All' })) return
  await Promise.allSettled(ids.map(id => api.delete(`/authorizations/${id}`)))
  selectedIds.value = new Set()
  load()
}

const userById       = computed(() => new Map(users.value.map(u => [u.id, u])))
const userGroupById  = computed(() => new Map(userGroups.value.map(g => [g.id, g])))
const hostById       = computed(() => new Map(hosts.value.map(h => [h.id, h])))
const hostGroupById  = computed(() => new Map(hostGroups.value.map(g => [g.id, g])))
const credentialById = computed(() => new Map(credentials.value.map(c => [c.id, c])))

function userName(id: string)       { return userById.value.get(id)?.username ?? id }
function userGroupName(id: string)  { return userGroupById.value.get(id)?.name ?? id }
function hostName(id: string)       { return hostById.value.get(id)?.name ?? id }
function hostGroupName(id: string)  { return hostGroupById.value.get(id)?.name ?? id }
function credentialName(id: string) { const c = credentialById.value.get(id); return c?.name || c?.username || id }
function credsOf(a: any): string[] { return (a.credential_ids && a.credential_ids.length) ? a.credential_ids : (a.credential_id ? [a.credential_id] : []) }
function formatDate(d: string)      { return new Date(d).toLocaleString() }

function principalsOf(a: any): { type: string; ids: string[] } {
  const u = (a.user_ids && a.user_ids.length) ? a.user_ids : (a.user_id ? [a.user_id] : [])
  if (u.length) return { type: 'user', ids: u }
  const g = (a.user_group_ids && a.user_group_ids.length) ? a.user_group_ids : (a.user_group_id ? [a.user_group_id] : [])
  if (g.length) return { type: 'user_group', ids: g }
  return { type: 'none', ids: [] }
}
function targetsOf(a: any): { type: string; ids: string[] } {
  const h = (a.host_ids && a.host_ids.length) ? a.host_ids : (a.host_id ? [a.host_id] : [])
  if (h.length) return { type: 'host', ids: h }
  const hg = (a.host_group_ids && a.host_group_ids.length) ? a.host_group_ids : (a.host_group_id ? [a.host_group_id] : [])
  if (hg.length) return { type: 'host_group', ids: hg }
  return { type: 'none', ids: [] }
}

async function load() {
  loading.value = true
  try {
    const [a, u, ug, h, hg, c] = await Promise.all([
      api.get('/authorizations'),
      api.get('/users'),
      api.get('/users/groups'),
      api.get('/hosts'),
      api.get('/host-groups'),
      api.get('/credentials'),
    ])
    authorizations.value = a.data
    users.value       = u.data
    userGroups.value  = ug.data
    hosts.value       = h.data
    hostGroups.value  = hg.data
    credentials.value = c.data
  } finally {
    loading.value = false
  }
}

// ── Expand state ──────────────────────────────────────────────────────────
const expandedId    = ref<string | null>(null)
const isCreate      = ref(false)
const principalType = ref<'user' | 'user_group'>('user')
const targetType    = ref<'host' | 'host_group'>('host')
const nameInputRef  = ref<HTMLInputElement | null>(null)

const form = reactive({
  name: '', principalIds: [] as string[], targetIds: [] as string[],
  credentialIds: [] as string[], actions: ['ssh'] as string[],
  date_start: '', date_expired: '', enabled: true,
})
const formError = ref('')
const saving    = ref(false)

const bulkCount = computed(() =>
  isCreate.value ? Math.max(1, form.principalIds.length) * Math.max(1, form.targetIds.length) : 1
)

// Select-all / clear-all for the principal + target checkbox grids (run-modal parity).
const _principalList = computed(() => principalType.value === 'user' ? users.value : userGroups.value)
const _targetList    = computed(() => targetType.value === 'host' ? hosts.value : hostGroups.value)
const allPrincipalsSel = computed(() => _principalList.value.length > 0 && _principalList.value.every((i: any) => form.principalIds.includes(i.id)))
const allTargetsSel    = computed(() => _targetList.value.length > 0 && _targetList.value.every((i: any) => form.targetIds.includes(i.id)))
function toggleAllPrincipals() {
  const ids = _principalList.value.map((i: any) => i.id)
  form.principalIds = allPrincipalsSel.value ? form.principalIds.filter(id => !ids.includes(id)) : Array.from(new Set([...form.principalIds, ...ids]))
}
function toggleAllTargets() {
  const ids = _targetList.value.map((i: any) => i.id)
  form.targetIds = allTargetsSel.value ? form.targetIds.filter(id => !ids.includes(id)) : Array.from(new Set([...form.targetIds, ...ids]))
}
const allCredsSel = computed(() => credentials.value.length > 0 && credentials.value.every((c: any) => form.credentialIds.includes(c.id)))
function toggleAllCreds() {
  form.credentialIds = allCredsSel.value ? [] : credentials.value.map((c: any) => c.id)
}

// Auto-fill name in create mode
watch([() => [...form.principalIds], () => [...form.targetIds]], () => {
  if (!isCreate.value || form.name) return
  const firstP = form.principalIds[0]
  const firstT = form.targetIds[0]
  if (!firstP || !firstT) return
  const pLabel = principalType.value === 'user'
    ? userById.value.get(firstP)?.username
    : userGroupById.value.get(firstP)?.name
  const tLabel = targetType.value === 'host'
    ? hostById.value.get(firstT)?.name
    : hostGroupById.value.get(firstT)?.name
  if (pLabel && tLabel) form.name = `${pLabel} → ${tLabel}`
})

function toDatetimeLocal(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function openCreate() {
  isCreate.value = true
  expandedId.value = '__new__'
  principalType.value = 'user'; targetType.value = 'host'
  Object.assign(form, { name: '', principalIds: [], targetIds: [], credentialIds: [], actions: ['ssh'], date_start: '', date_expired: '', enabled: true })
  formError.value = ''
  nextTick(() => nameInputRef.value?.focus())
}

function openEdit(a: any) {
  isCreate.value = false
  expandedId.value = a.id
  const aUsers = (a.user_ids && a.user_ids.length) ? a.user_ids : (a.user_id ? [a.user_id] : [])
  const aUGroups = (a.user_group_ids && a.user_group_ids.length) ? a.user_group_ids : (a.user_group_id ? [a.user_group_id] : [])
  const aHosts = (a.host_ids && a.host_ids.length) ? a.host_ids : (a.host_id ? [a.host_id] : [])
  const aHGroups = (a.host_group_ids && a.host_group_ids.length) ? a.host_group_ids : (a.host_group_id ? [a.host_group_id] : [])
  principalType.value = aUsers.length ? 'user' : (aUGroups.length ? 'user_group' : 'user')
  targetType.value    = aHosts.length ? 'host' : (aHGroups.length ? 'host_group' : 'host')
  form.principalIds   = principalType.value === 'user' ? [...aUsers] : [...aUGroups]
  form.targetIds      = targetType.value === 'host' ? [...aHosts] : [...aHGroups]
  form.credentialIds  = (a.credential_ids && a.credential_ids.length) ? [...a.credential_ids] : (a.credential_id ? [a.credential_id] : [])
  form.actions        = [...(a.actions || [])]
  form.date_start     = toDatetimeLocal(a.date_start)
  form.date_expired   = toDatetimeLocal(a.date_expired)
  form.enabled        = a.enabled
  form.name           = a.name
  formError.value     = ''
  nextTick(() => nameInputRef.value?.focus())
}

function closeExpand() { expandedId.value = null; formError.value = '' }

async function save() {
  if (!form.principalIds.length) { formError.value = 'Select at least one user or group'; return }
  if (!form.targetIds.length)    { formError.value = 'Select at least one host or host group'; return }
  if (!form.actions.length)      { formError.value = 'Select at least one action'; return }
  saving.value = true; formError.value = ''
  try {
    const dateStart   = form.date_start   ? new Date(form.date_start).toISOString()   : null
    const dateExpired = form.date_expired ? new Date(form.date_expired).toISOString() : null
    const payload: any = {
      name: form.name || 'rule',
      user_ids:        principalType.value === 'user'       ? form.principalIds : [],
      user_group_ids:  principalType.value === 'user_group' ? form.principalIds : [],
      host_ids:        targetType.value    === 'host'       ? form.targetIds    : [],
      host_group_ids:  targetType.value    === 'host_group' ? form.targetIds    : [],
      credential_ids:  form.credentialIds,
      credential_id:   form.credentialIds[0] || null,
      actions:         form.actions,
      date_start: dateStart, date_expired: dateExpired, enabled: form.enabled,
    }
    if (!isCreate.value) {
      await api.put(`/authorizations/${expandedId.value}`, payload)
    } else {
      await api.post('/authorizations', payload)
    }
    closeExpand(); load()
  } catch (e: any) {
    formError.value = e?.response?.data?.detail || 'Failed to save authorization'
  } finally {
    saving.value = false
  }
}

async function remove(a: any) {
  if (!await confirm(`Delete authorization "${a.name}"?`, { title: 'Delete Authorization', danger: true, confirmLabel: 'Delete' })) return
  try { await api.delete(`/authorizations/${a.id}`); load() }
  catch (e: any) { alert(e?.response?.data?.detail || 'Failed to delete authorization') }
}

async function approve(a: any) {
  try { await api.post(`/authorizations/${a.id}/approve`); load() }
  catch (e: any) { alert(e?.response?.data?.detail || 'Failed to approve authorization') }
}

async function reject(a: any) {
  if (!await confirm(`Reject authorization "${a.name}"? It will stay disabled.`, { title: 'Reject Authorization', danger: true, confirmLabel: 'Reject' })) return
  try { await api.post(`/authorizations/${a.id}/reject`); load() }
  catch (e: any) { alert(e?.response?.data?.detail || 'Failed to reject authorization') }
}

onMounted(load)
</script>

<style scoped>
/* ── Table ──────────────────────────────────────────────────────────────────── */
.row-active    td { background: rgba(88,166,255,0.04); }
.row-selected  td { background: rgba(88,166,255,0.07); }
.bulk-sel-badge {
  padding: 2px 10px; border-radius: 99px;
  background: rgba(88,166,255,0.15); border: 1px solid rgba(88,166,255,0.35);
  font-size: 12px; color: #58a6ff; font-weight: 600;
}

/* ── Expand row ─────────────────────────────────────────────────────────────── */
.expand-row > td { padding: 0 !important; }
.expand-form {
  border-top: 2px solid var(--accent2);
  animation: expand-in 0.18s ease;
}
@keyframes expand-in { from { opacity: 0; transform: translateY(-8px); } to { opacity: 1; transform: none; } }

.expand-form-head {
  padding: 12px 20px; background: var(--bg2);
  font-size: 13px; font-weight: 600;
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between;
}
.expand-body   { padding: 16px 20px; display: flex; flex-direction: column; gap: 12px; background: var(--bg3); }
.expand-footer {
  padding: 10px 20px; border-top: 1px solid var(--border); background: var(--bg2);
  display: flex; align-items: center; justify-content: space-between;
}

/* ── Form layout ────────────────────────────────────────────────────────────── */
.eg-row { display: flex; flex-wrap: wrap; gap: 12px; }
.eg-row--cols4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; align-items: start; }
@media (max-width: 1300px) { .eg-row--cols4 { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 760px)  { .eg-row--cols4 { grid-template-columns: 1fr; } }
.eg-field { display: flex; flex-direction: column; gap: 4px; }
.eg-field--wide { flex: 1; min-width: 200px; }
.eg-label { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text2); }
.eg-opt   { font-weight: 400; text-transform: none; font-size: 10px; letter-spacing: 0; }
.eg-hint  { font-size: 11px; color: var(--text2); }
.eg-error { font-size: 12px; color: var(--danger); padding: 4px 0; }
.eg-bulk-note {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; background: rgba(59,130,246,0.08); border: 1px solid rgba(59,130,246,0.3); border-radius: 6px;
  font-size: 12px; color: var(--accent2);
}
.bulk-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent2); box-shadow: 0 0 5px var(--accent2); flex-shrink: 0; }

/* ── Sections (checkbox columns) ────────────────────────────────────────────── */
.eg-section { border: 1px solid var(--border); border-radius: 7px; overflow: hidden; display: flex; flex-direction: column; background: var(--bg2); }
.eg-section-head {
  padding: 8px 12px; background: rgba(0,0,0,0.15); border-bottom: 1px solid var(--border);
  font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: var(--text2);
  display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-shrink: 0;
}
.eg-toggle-group { display: flex; background: var(--bg3); border: 1px solid var(--border); border-radius: 5px; overflow: hidden; }
.eg-toggle {
  padding: 3px 10px; font-size: 11px; font-weight: 500;
  background: transparent; border: none; color: var(--text2); cursor: pointer;
  transition: color 0.15s, background 0.15s;
}
.eg-toggle.active { background: var(--bg2); color: var(--text); }
.eg-toggle:hover:not(.active) { color: var(--text); }

/* ── Checkbox grid ──────────────────────────────────────────────────────────── */
.check-grid { padding: 6px; overflow-y: auto; max-height: 260px; display: grid; grid-template-columns: 1fr; align-content: start; gap: 3px; }
.check-item {
  display: flex; align-items: flex-start; gap: 8px; padding: 6px 8px;
  border-radius: 5px; cursor: pointer; border: 1px solid transparent;
  transition: background 0.12s, border-color 0.12s;
}
.check-item:hover       { background: rgba(88,166,255,0.07); border-color: rgba(88,166,255,0.2); }
.check-item--sel        { background: rgba(88,166,255,0.1); border-color: rgba(88,166,255,0.35) !important; }
.check-item input[type="checkbox"] { accent-color: #58a6ff; width: 14px; height: 14px; flex-shrink: 0; margin-top: 2px; cursor: pointer; }
.check-item-body { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.check-item-name { font-size: 12px; font-weight: 600; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.check-item-sub  { font-size: 10px; color: var(--text2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.check-sel-hint  { padding: 4px 10px 8px; font-size: 11px; color: var(--text2); border-top: 1px solid var(--border); flex-shrink: 0; }
.check-all-link  { color: #58a6ff; cursor: pointer; font-weight: 600; }
.check-all-link:hover { text-decoration: underline; }
.check-empty     { font-size: 12px; color: var(--text2); padding: 8px; text-align: center; }

/* ── Credential pick-list (single-select checkbox style) ────────────────────── */
.cred-pick-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); align-content: start; gap: 4px; max-height: 220px; overflow-y: auto; border: 1px solid var(--border); border-radius: 6px; padding: 4px; background: var(--bg3); }
.cred-pick { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-radius: 5px; cursor: pointer; border: 1px solid transparent; font-size: 12px; color: var(--text); }
.cred-pick:hover { background: rgba(88,166,255,0.07); border-color: rgba(88,166,255,0.2); }
.cred-pick--sel { background: rgba(88,166,255,0.1); border-color: rgba(88,166,255,0.35) !important; }
.cred-pick input[type="checkbox"] { accent-color: #58a6ff; width: 14px; height: 14px; flex-shrink: 0; cursor: pointer; }
.cred-pick-meta { color: var(--text2); font-size: 11px; }

/* ── Actions checkboxes ─────────────────────────────────────────────────────── */
.act-check-row { display: flex; flex-wrap: wrap; gap: 6px; }
.act-check {
  display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px;
  border-radius: 4px; border: 1px solid var(--border); background: var(--bg3);
  cursor: pointer; font-size: 12px; color: var(--text); user-select: none; transition: border-color 0.12s;
}
.act-check:hover { border-color: var(--accent2); }
.act-check input { accent-color: var(--accent2); cursor: pointer; }
</style>
