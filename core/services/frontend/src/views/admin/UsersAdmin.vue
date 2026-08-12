<template>
  <div>
    <!-- Users card -->
    <div class="card" style="margin-bottom:20px">
      <div class="card-header">
        Users
        <div style="display:flex;gap:8px;align-items:center">
          <button v-if="auth.isSuperAdmin && selectedUserIds.size" class="btn btn-sm" style="color:var(--danger);border-color:var(--danger)" @click="bulkDeleteUsers">
            ✕ Delete {{ selectedUserIds.size }} selected
          </button>
          <button class="btn btn-sm" :disabled="syncingUsers" @click="syncUsersFromZabbix" title="Pull users &amp; groups from Zabbix">
            {{ syncingUsers ? 'Syncing…' : '⟳ Zabbix Sync' }}
          </button>
          <button v-if="auth.isSuperAdmin" class="btn btn-primary btn-sm" @click="openCreateUser">+ User</button>
        </div>
      </div>
      <div v-if="syncError" class="fp-error" style="padding:8px 16px">{{ syncError }}</div>
      <table class="table">
        <thead>
          <tr>
            <th v-if="auth.isSuperAdmin" style="width:32px">
              <input type="checkbox" :checked="allDeletableUsersSelected" @change="toggleSelectAllUsers" />
            </th>
            <th>Username</th><th>Display Name</th><th>Email</th><th>Role</th><th>Status</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id" :class="{ 'row-active': activeUserId === u.id }">
            <td v-if="auth.isSuperAdmin">
              <input
                v-if="!u.zabbix_userid"
                type="checkbox"
                :checked="selectedUserIds.has(u.id)"
                @change="toggleUserSelect(u.id)"
              />
            </td>
            <td style="font-weight:600">
              <span v-if="u.zabbix_userid" class="src-badge src-badge--zbx" title="Synced from Zabbix">Z</span>
              <span v-else class="src-badge src-badge--sr" title="SeyalRun native user">S</span>
              {{ u.username }}
            </td>
            <td>{{ u.display_name || '—' }}</td>
            <td style="color:var(--text2)">{{ u.email || '—' }}</td>
            <td>
              <span v-for="rn in (u.roles && u.roles.length ? u.roles : (u.role_name ? [u.role_name] : []))" :key="rn" class="badge badge-blue" style="margin-right:3px">{{ rn }}</span>
              <span v-if="!(u.roles && u.roles.length) && !u.role_name" style="color:var(--text2)">—</span>
            </td>
            <td>
              <span v-if="u.is_active" class="badge badge-green">Active</span>
              <span v-else class="badge badge-gray">Disabled</span>
            </td>
            <td>
              <div style="display:flex;gap:8px;justify-content:flex-end">
                <button class="btn-pill btn-pill-outline" @click="openEditUser(u)">✎ Edit</button>
                <button
                  v-if="auth.isSuperAdmin && u.mfa_method"
                  class="btn-pill btn-pill-outline"
                  title="Clear this user's MFA enrollment so they can re-enroll"
                  @click="resetUserMfa(u)"
                >⟲ Reset MFA</button>
                <button
                  v-if="auth.isSuperAdmin && !u.zabbix_userid"
                  class="btn-pill btn-pill-outline"
                  style="color:var(--danger);border-color:var(--danger)"
                  @click="deleteUser(u)"
                >✕ Delete</button>
                <span
                  v-else-if="auth.isSuperAdmin"
                  class="btn-pill"
                  style="opacity:0.4;cursor:not-allowed"
                  title="Zabbix-synced users can't be deleted here — remove in Zabbix and re-sync"
                >✕ Delete</span>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="loadingUsers" style="padding:24px;text-align:center;color:var(--text2)">Loading…</div>
    </div>

    <!-- Groups card -->
    <div class="card">
      <div class="card-header">
        User Groups
        <div style="display:flex;gap:8px;align-items:center">
          <button v-if="auth.isSuperAdmin && selectedGroupIds.size" class="btn btn-sm" style="color:var(--danger);border-color:var(--danger)" @click="bulkDeleteGroups">
            ✕ Delete {{ selectedGroupIds.size }} selected
          </button>
          <button v-if="auth.isSuperAdmin" class="btn btn-primary btn-sm" @click="openCreateGroup">+ Group</button>
        </div>
      </div>
      <table class="table">
        <thead>
          <tr>
            <th v-if="auth.isSuperAdmin" style="width:32px">
              <input type="checkbox" :checked="allDeletableGroupsSelected" @change="toggleSelectAllGroups" />
            </th>
            <th>Name</th><th>Description</th><th>Members</th><th>Created</th><th></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="g in groups" :key="g.id">
            <tr :class="{ 'row-active': expandedGroupId === g.id || expandedRolesGroupId === g.id }">
              <td v-if="auth.isSuperAdmin">
                <input
                  v-if="!g.zabbix_usrgrpid"
                  type="checkbox"
                  :checked="selectedGroupIds.has(g.id)"
                  @change="toggleGroupSelect(g.id)"
                />
              </td>
              <td style="font-weight:600">
                <span v-if="g.zabbix_usrgrpid" class="src-badge src-badge--zbx" title="Synced from Zabbix">Z</span>
                <span v-else class="src-badge src-badge--sr" title="SeyalRun native group">S</span>
                {{ g.name }}
                <span v-if="g.policies?.mfa_enforced" class="badge badge-blue" style="margin-left:4px;font-size:10px" title="Members must set up MFA">MFA</span>
              </td>
              <td style="color:var(--text2)">{{ g.description || '—' }}</td>
              <td style="color:var(--text2);font-size:12px">{{ g.member_count ?? '—' }}</td>
              <td style="color:var(--text2);font-size:12px">{{ formatDate(g.created_at) }}</td>
              <td>
                <div style="display:flex;gap:8px;justify-content:flex-end">
                  <button
                    :class="['btn-pill', expandedRolesGroupId === g.id ? 'btn-pill-active' : 'btn-pill-outline']"
                    @click="toggleRoles(g)"
                  >&#128273; Roles {{ expandedRolesGroupId === g.id ? '▲' : '▼' }}</button>
                  <button
                    :class="['btn-pill', expandedGroupId === g.id ? 'btn-pill-active' : 'btn-pill-outline']"
                    @click="toggleMembers(g)"
                  >&#128101; Members {{ expandedGroupId === g.id ? '▲' : '▼' }}</button>
                  <button class="btn-pill btn-pill-outline" @click="openEditGroup(g)">✎ Edit</button>
                  <button
                    v-if="auth.isSuperAdmin && !g.zabbix_usrgrpid"
                    class="btn-pill btn-pill-outline"
                    style="color:var(--danger);border-color:var(--danger)"
                    @click="deleteGroup(g)"
                  >✕ Delete</button>
                  <span
                    v-else-if="auth.isSuperAdmin"
                    class="btn-pill"
                    style="opacity:0.4;cursor:not-allowed"
                    title="Zabbix-synced groups can't be deleted here — remove in Zabbix and re-sync"
                  >✕ Delete</span>
                </div>
              </td>
            </tr>
            <!-- Inline role-assignment row — this is how a group's members inherit a role -->
            <tr v-if="expandedRolesGroupId === g.id" class="members-expand-row">
              <td colspan="6" style="padding:0">
                <div class="roles-expand">
                  <div class="roles-expand-title">Roles granted to — {{ g.name }} <span style="text-transform:none;font-weight:400">(members inherit these)</span></div>
                  <div class="role-check-list">
                    <label v-for="r in roles" :key="r.id" class="role-check" :class="{ checked: checkedRoleIds.has(r.id) }" :title="r.description">
                      <input type="checkbox" :checked="checkedRoleIds.has(r.id)" @change="toggleRole(r.id)" />
                      <span class="role-check-name">{{ r.name }}</span>
                      <span class="role-check-desc">{{ r.description }}</span>
                    </label>
                  </div>
                  <div v-if="rolesError" class="fp-error">{{ rolesError }}</div>
                  <div class="roles-expand-footer">
                    <button class="btn" @click="closeRoles">Cancel</button>
                    <button class="btn btn-primary" @click="saveRoles" :disabled="savingRoles">
                      {{ savingRoles ? 'Saving…' : 'Save Roles' }}
                    </button>
                  </div>
                </div>
              </td>
            </tr>
            <!-- Inline member management row -->
            <tr v-if="expandedGroupId === g.id" class="members-expand-row">
              <td colspan="6" style="padding:0">
                <div class="members-expand">
                  <div class="members-expand-header">
                    <span class="members-expand-title">Members — {{ g.name }}</span>
                    <span style="font-size:12px;color:var(--text2)">{{ checkedMemberIds.size }} selected</span>
                  </div>
                  <input
                    v-model="memberSearch"
                    class="members-search"
                    placeholder="Search users…"
                    autofocus
                  />
                  <div class="members-list">
                    <label
                      v-for="u in filteredMemberUsers"
                      :key="u.id"
                      class="members-row"
                      :class="{ checked: checkedMemberIds.has(u.id) }"
                    >
                      <input
                        type="checkbox"
                        class="members-checkbox"
                        :checked="checkedMemberIds.has(u.id)"
                        @change="toggleMember(u.id)"
                      />
                      <span class="members-username">{{ u.username }}</span>
                      <span v-if="u.display_name && u.display_name !== u.username" class="members-display">{{ u.display_name }}</span>
                    </label>
                    <div v-if="filteredMemberUsers.length === 0" style="padding:12px;color:var(--text2);font-size:13px;text-align:center">
                      No users found
                    </div>
                  </div>
                  <div v-if="membersError" class="fp-error" style="padding:0 12px 8px">{{ membersError }}</div>
                  <div class="members-expand-footer">
                    <button class="btn" @click="closeMembers">Cancel</button>
                    <button class="btn btn-primary" @click="saveMembers" :disabled="savingMembers">
                      {{ savingMembers ? 'Saving…' : 'Save Members' }}
                    </button>
                  </div>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      <div v-if="loadingGroups" style="padding:24px;text-align:center;color:var(--text2)">Loading…</div>
    </div>

    <!-- ── User panel ───────────────────────────────────────────────────── -->
    <SlidePanel
      v-model="showUserPanel"
      :title="editingUser ? 'Edit User' : 'Add User'"
      :subtitle="editingUser?.username"
      :width="420"
      @close="closeUserPanel"
    >
      <div class="fp-form">
        <div class="fp-field">
          <label class="fp-label">Username</label>
          <input v-model="userForm.username" class="fp-input" :disabled="!!editingUser" placeholder="jdoe" />
        </div>
        <div class="fp-field">
          <label class="fp-label">Display Name</label>
          <input v-model="userForm.display_name" class="fp-input" placeholder="Jane Doe" />
        </div>
        <div class="fp-field">
          <label class="fp-label">Email</label>
          <input v-model="userForm.email" class="fp-input" type="email" placeholder="jane@example.com" />
        </div>
        <div class="fp-field">
          <label class="fp-label">Password{{ editingUser ? ' (leave blank to keep current)' : '' }}</label>
          <input v-model="userForm.password" type="password" class="fp-input" placeholder="••••••••" autocomplete="new-password" />
        </div>
        <div class="fp-field">
          <label class="fp-label">Roles <span class="fp-opt">(a user can hold multiple — zero-trust: no role = no access)</span></label>
          <div class="role-check-list">
            <label v-for="r in roles" :key="r.id" class="role-check" :class="{ checked: userForm.role_ids.includes(r.id) }" :title="r.description">
              <input type="checkbox" :value="r.id" v-model="userForm.role_ids" />
              <span class="role-check-name">{{ r.name }}</span>
              <span class="role-check-desc">{{ r.description }}</span>
            </label>
          </div>
        </div>
        <div v-if="editingUser" class="fp-field">
          <label class="fp-label">Status</label>
          <div class="fp-toggle-group">
            <button :class="['fp-toggle', userForm.is_active && 'active']" @click="userForm.is_active = true">Active</button>
            <button :class="['fp-toggle', !userForm.is_active && 'active']" @click="userForm.is_active = false">Disabled</button>
          </div>
        </div>
        <template v-if="editingUser">
          <div class="fp-section-head">Security Policies</div>
          <label class="opt-row">
            <input type="checkbox" v-model="userForm.single_session_enabled" />
            <span><b>Enforce single session</b> — logging in elsewhere ends this user's other session immediately.</span>
          </label>
          <label class="opt-row">
            <input type="checkbox" v-model="userForm.ip_restriction_enabled" />
            <span><b>Restrict login IPs</b> — only allow login from the IPs below.</span>
          </label>
          <div v-if="userForm.ip_restriction_enabled" class="fp-field">
            <label class="fp-label">Allowed login IPs <span class="fp-opt">(CIDR, one per line)</span></label>
            <textarea v-model="userForm.allowed_ips_text" class="fp-input" rows="2" placeholder="203.0.113.4/32" style="resize:vertical;font-family:inherit"></textarea>
          </div>
        </template>

        <!-- Groups assignment -->
        <div class="fp-section-head">Group Memberships</div>
        <div class="fp-field">
          <AsyncPicker v-model="userGroupsPicker" :search-fn="searchGroups" :multiple="true" placeholder="Assign to groups…" />
        </div>

        <div v-if="userError" class="fp-error">{{ userError }}</div>
      </div>

      <template #footer>
        <button class="btn" @click="closeUserPanel">Cancel</button>
        <button class="btn btn-primary" @click="saveUser" :disabled="savingUser">{{ savingUser ? 'Saving…' : (editingUser ? 'Save' : 'Create') }}</button>
      </template>
    </SlidePanel>

    <!-- ── Group panel ──────────────────────────────────────────────────── -->
    <SlidePanel
      v-model="showGroupPanel"
      :title="editingGroup ? 'Edit Group' : 'Add Group'"
      :subtitle="editingGroup?.name"
      :width="420"
      @close="closeGroupPanel"
    >
      <div class="fp-form">
        <div class="fp-field">
          <label class="fp-label">Name</label>
          <input v-model="groupForm.name" class="fp-input" placeholder="e.g. db-admins" />
        </div>
        <div class="fp-field">
          <label class="fp-label">Description</label>
          <input v-model="groupForm.description" class="fp-input" placeholder="Optional description" />
        </div>

        <div class="fp-section-head">Group Policies</div>
        <label class="opt-row">
          <input type="checkbox" v-model="groupForm.mfa_enforced" :disabled="!auth.isSuperAdmin" />
          <span>
            <b>Require MFA for this group</b>
            <span v-if="!auth.isSuperAdmin" style="color:var(--text2)"> (superadmin only)</span>
            — members without MFA are blocked from everything except enrollment until they set it up.
          </span>
        </label>
        <label class="opt-row">
          <input type="checkbox" v-model="groupForm.setup_wizard" />
          <span><b>Show setup wizard for new members</b> — a one-time guided first-login flow.</span>
        </label>
        <label class="opt-row">
          <input type="checkbox" v-model="groupForm.notifications_enabled" />
          <span><b>Notify by email</b> — mail job-run alerts for this group's members to the addresses below.</span>
        </label>
        <template v-if="groupForm.notifications_enabled">
          <div class="fp-field">
            <label class="fp-label">Recipient emails (one per line)</label>
            <textarea v-model="groupForm.notify_emails_text" class="fp-input" rows="3" placeholder="oncall@example.com" style="resize:vertical;font-family:inherit"></textarea>
          </div>
          <div class="fp-field">
            <label class="fp-label">Minimum severity</label>
            <select v-model="groupForm.notify_min_severity" class="fp-input">
              <option value="info">Info and above (everything)</option>
              <option value="medium">Medium and above</option>
              <option value="critical">Critical only</option>
            </select>
          </div>
        </template>
        <label class="opt-row">
          <input type="checkbox" v-model="groupForm.single_session_enabled" />
          <span><b>Enforce single session</b> — members are logged out of any other session the moment they log in elsewhere.</span>
        </label>
        <label class="opt-row">
          <input type="checkbox" v-model="groupForm.ip_restriction_enabled" />
          <span><b>Restrict login IPs</b> — members may only log in from the IPs below. Combines with any individual restriction of theirs — both must match.</span>
        </label>
        <template v-if="groupForm.ip_restriction_enabled">
          <div class="fp-field">
            <label class="fp-label">Allowed IPs (CIDR, one per line)</label>
            <textarea v-model="groupForm.ip_cidrs_text" class="fp-input" rows="2" placeholder="203.0.113.0/24" style="resize:vertical;font-family:inherit"></textarea>
          </div>
        </template>

        <div v-if="groupError" class="fp-error">{{ groupError }}</div>
      </div>
      <template #footer>
        <button class="btn" @click="closeGroupPanel">Cancel</button>
        <button class="btn btn-primary" @click="saveGroup" :disabled="savingGroup">{{ savingGroup ? 'Saving…' : (editingGroup ? 'Save' : 'Create') }}</button>
      </template>
    </SlidePanel>

    <!-- ── New accounts from sync — default password shown ONCE ──────────── -->
    <div v-if="newAccounts.length" class="modal-overlay" @click.self="newAccounts = []">
      <div class="modal" style="max-width:480px">
        <div class="modal-header">{{ newAccounts.length }} new account{{ newAccounts.length === 1 ? '' : 's' }} provisioned</div>
        <div class="modal-body">
          <p style="font-size:13px;color:var(--text2);margin-bottom:12px">
            Hand these out — the password is shown only this once. Each account must change it at first login.
          </p>
          <div v-for="a in newAccounts" :key="a.username" class="new-account-row">
            <span class="new-account-user">{{ a.username }}</span>
            <code class="new-account-pw">{{ a.default_password }}</code>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-primary" @click="newAccounts = []">Done</button>
        </div>
      </div>
    </div>

  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import AsyncPicker, { type PickerItem } from '@/components/common/AsyncPicker.vue'
import SlidePanel from '@/components/common/SlidePanel.vue'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useConfirm } from '@/composables/useConfirm'

const auth = useAuthStore()
const { confirm } = useConfirm()

const users = ref<any[]>([])
const groups = ref<any[]>([])
const roles = ref<any[]>([])
const loadingUsers = ref(false)
const loadingGroups = ref(false)
const syncingUsers = ref(false)
const syncError = ref('')
const newAccounts = ref<{ username: string; default_password: string }[]>([])

// ── Bulk select/delete (superadmin only; Zabbix-synced rows are never selectable) ──
const selectedUserIds = ref<Set<string>>(new Set())
const selectedGroupIds = ref<Set<string>>(new Set())

const deletableUsers = computed(() => users.value.filter((u) => !u.zabbix_userid))
const allDeletableUsersSelected = computed(() =>
  deletableUsers.value.length > 0 && deletableUsers.value.every((u) => selectedUserIds.value.has(u.id))
)
function toggleUserSelect(id: string) {
  const s = new Set(selectedUserIds.value)
  s.has(id) ? s.delete(id) : s.add(id)
  selectedUserIds.value = s
}
function toggleSelectAllUsers() {
  selectedUserIds.value = allDeletableUsersSelected.value
    ? new Set()
    : new Set(deletableUsers.value.map((u) => u.id))
}
async function bulkDeleteUsers() {
  const n = selectedUserIds.value.size
  if (!n) return
  if (!await confirm(`Delete ${n} selected user${n === 1 ? '' : 's'}? This cannot be undone.`, { title: 'Delete Users', danger: true, confirmLabel: 'Delete' })) return
  const failures: string[] = []
  for (const id of selectedUserIds.value) {
    try { await api.delete(`/users/${id}`) }
    catch (e: any) { failures.push(e?.response?.data?.detail || id) }
  }
  selectedUserIds.value = new Set()
  await loadUsers()
  if (failures.length) alert(`${failures.length} of ${n} failed:\n${failures.join('\n')}`)
}

const deletableGroups = computed(() => groups.value.filter((g) => !g.zabbix_usrgrpid))
const allDeletableGroupsSelected = computed(() =>
  deletableGroups.value.length > 0 && deletableGroups.value.every((g) => selectedGroupIds.value.has(g.id))
)
function toggleGroupSelect(id: string) {
  const s = new Set(selectedGroupIds.value)
  s.has(id) ? s.delete(id) : s.add(id)
  selectedGroupIds.value = s
}
function toggleSelectAllGroups() {
  selectedGroupIds.value = allDeletableGroupsSelected.value
    ? new Set()
    : new Set(deletableGroups.value.map((g) => g.id))
}
async function bulkDeleteGroups() {
  const n = selectedGroupIds.value.size
  if (!n) return
  if (!await confirm(`Delete ${n} selected group${n === 1 ? '' : 's'}? This cannot be undone.`, { title: 'Delete Groups', danger: true, confirmLabel: 'Delete' })) return
  const failures: string[] = []
  for (const id of selectedGroupIds.value) {
    try { await api.delete(`/users/groups/${id}`) }
    catch (e: any) { failures.push(e?.response?.data?.detail || id) }
  }
  selectedGroupIds.value = new Set()
  await loadGroups()
  if (failures.length) alert(`${failures.length} of ${n} failed:\n${failures.join('\n')}`)
}

async function syncUsersFromZabbix() {
  syncingUsers.value = true
  syncError.value = ''
  try {
    const { data } = await api.post('/users/sync-from-zabbix')
    if (data.note) {
      syncError.value = data.note
    } else {
      newAccounts.value = data.new_accounts || []
      await Promise.all([loadUsers(), loadGroups()])
    }
  } catch (e: any) {
    syncError.value = e?.response?.data?.detail || 'Zabbix sync failed'
  } finally {
    syncingUsers.value = false
  }
}

function formatDate(d: string) { return new Date(d).toLocaleDateString() }

async function loadUsers() {
  loadingUsers.value = true
  try { const { data } = await api.get('/users'); users.value = data }
  finally { loadingUsers.value = false }
}
async function loadGroups() {
  loadingGroups.value = true
  try { const { data } = await api.get('/users/groups'); groups.value = data }
  finally { loadingGroups.value = false }
}
async function loadRoles() {
  try { const { data } = await api.get('/roles'); roles.value = data } catch {}
}

async function deleteUser(u: any) {
  if (!await confirm(`Delete user "${u.username}"? This cannot be undone.`, { title: 'Delete User', danger: true, confirmLabel: 'Delete' })) return
  try { await api.delete(`/users/${u.id}`); await loadUsers() }
  catch (e: any) { alert(e?.response?.data?.detail || 'Failed to delete user') }
}

async function resetUserMfa(u: any) {
  if (!await confirm(`Reset MFA for "${u.username}"? They will need to re-enroll from scratch.`, { title: 'Reset MFA', danger: true, confirmLabel: 'Reset MFA' })) return
  try { await api.post(`/users/${u.id}/mfa/reset`); await loadUsers() }
  catch (e: any) { alert(e?.response?.data?.detail || 'Failed to reset MFA') }
}

async function deleteGroup(g: any) {
  if (!await confirm(`Delete group "${g.name}"? This cannot be undone.`, { title: 'Delete Group', danger: true, confirmLabel: 'Delete' })) return
  try { await api.delete(`/users/groups/${g.id}`); await loadGroups() }
  catch (e: any) { alert(e?.response?.data?.detail || 'Failed to delete group') }
}

// ── User panel ─────────────────────────────────────────────────────────────
const showUserPanel = ref(false)
const editingUser = ref<any>(null)
const activeUserId = ref<string | null>(null)
const userForm = reactive({
  username: '', display_name: '', email: '', password: '', role_ids: [] as string[], is_active: true,
  allowed_ips_text: '', ip_restriction_enabled: false, single_session_enabled: false,
})
const userGroupsPicker = ref<PickerItem[]>([])
const userError = ref('')
const savingUser = ref(false)

function openCreateUser() {
  editingUser.value = null
  activeUserId.value = null
  Object.assign(userForm, {
    username: '', display_name: '', email: '', password: '', role_ids: [], is_active: true,
    allowed_ips_text: '', ip_restriction_enabled: false, single_session_enabled: false,
  })
  userGroupsPicker.value = []
  userError.value = ''
  showUserPanel.value = true
}

function openEditUser(u: any) {
  editingUser.value = u
  activeUserId.value = u.id
  Object.assign(userForm, {
    username: u.username, display_name: u.display_name || '', email: u.email || '',
    password: '', role_ids: [...(u.role_ids || (u.role_id ? [u.role_id] : []))], is_active: u.is_active,
    allowed_ips_text: (u.allowed_ips || []).join('\n'),
    ip_restriction_enabled: !!u.ip_restriction_enabled,
    single_session_enabled: !!u.single_session_enabled,
  })
  // Pre-fill group memberships
  userGroupsPicker.value = groups.value
    .filter(g => g.user_ids?.includes(u.id))
    .map(g => ({ id: g.id, label: g.name }))
  userError.value = ''
  showUserPanel.value = true
}

function closeUserPanel() { showUserPanel.value = false; editingUser.value = null; activeUserId.value = null }

async function saveUser() {
  savingUser.value = true
  userError.value = ''
  try {
    let userId = editingUser.value?.id
    if (editingUser.value) {
      const payload: any = {
        display_name: userForm.display_name,
        email: userForm.email,
        role_ids: userForm.role_ids,
        is_active: userForm.is_active,
        allowed_ips: userForm.allowed_ips_text.split('\n').map(s => s.trim()).filter(Boolean),
        ip_restriction_enabled: userForm.ip_restriction_enabled,
        single_session_enabled: userForm.single_session_enabled,
      }
      if (userForm.password) payload.password = userForm.password
      await api.put(`/users/${userId}`, payload)
    } else {
      const resp = await api.post('/users', {
        username: userForm.username,
        display_name: userForm.display_name,
        email: userForm.email,
        password: userForm.password,
        role_ids: userForm.role_ids,
      })
      userId = resp.data.id
    }
    // Sync group memberships — add user to selected groups
    for (const g of userGroupsPicker.value) {
      const grp = groups.value.find(x => x.id === g.id)
      if (!grp?.user_ids?.includes(userId)) {
        const existingIds = grp?.user_ids || []
        await api.put(`/users/groups/${g.id}/members`, { user_ids: [...existingIds, userId] }).catch(() => {})
      }
    }
    closeUserPanel()
    loadUsers()
    loadGroups()
  } catch (e: any) {
    userError.value = e?.response?.data?.detail || 'Failed to save user'
  } finally {
    savingUser.value = false
  }
}

// ── Group panel ────────────────────────────────────────────────────────────
const showGroupPanel = ref(false)
const editingGroup = ref<any>(null)
const groupForm = reactive({
  name: '', description: '', mfa_enforced: false, setup_wizard: false, notifications_enabled: false,
  notify_emails_text: '', notify_min_severity: 'medium',
  single_session_enabled: false, ip_restriction_enabled: false, ip_cidrs_text: '',
})
const groupError = ref('')
const savingGroup = ref(false)

function openCreateGroup() {
  editingGroup.value = null
  Object.assign(groupForm, {
    name: '', description: '', mfa_enforced: false, setup_wizard: false, notifications_enabled: false,
    notify_emails_text: '', notify_min_severity: 'medium',
    single_session_enabled: false, ip_restriction_enabled: false, ip_cidrs_text: '',
  })
  groupError.value = ''
  showGroupPanel.value = true
}

async function openEditGroup(g: any) {
  editingGroup.value = g
  Object.assign(groupForm, {
    name: g.name, description: g.description || '',
    mfa_enforced: !!g.policies?.mfa_enforced,
    setup_wizard: !!g.policies?.setup_wizard,
    notifications_enabled: !!g.policies?.notifications_enabled,
    notify_emails_text: '', notify_min_severity: 'medium',
    single_session_enabled: !!g.policies?.single_session_enabled,
    ip_restriction_enabled: !!g.policies?.ip_restriction_enabled,
    ip_cidrs_text: '',
  })
  groupError.value = ''
  showGroupPanel.value = true
  if (g.policies?.notifications_enabled) {
    try {
      const { data } = await api.get(`/users/groups/${g.id}/notify-config`)
      groupForm.notify_emails_text = (data.emails || []).join('\n')
      groupForm.notify_min_severity = data.min_severity || 'medium'
    } catch { /* leave defaults if the fetch fails */ }
  }
  if (g.policies?.ip_restriction_enabled) {
    try {
      const { data } = await api.get(`/users/groups/${g.id}/ip-restriction`)
      groupForm.ip_cidrs_text = (data.cidrs || []).join('\n')
    } catch { /* leave defaults if the fetch fails */ }
  }
}

function closeGroupPanel() { showGroupPanel.value = false; editingGroup.value = null }

async function saveGroup() {
  savingGroup.value = true
  groupError.value = ''
  try {
    let groupId = editingGroup.value?.id
    if (editingGroup.value) {
      await api.put(`/users/groups/${groupId}`, { name: groupForm.name, description: groupForm.description })
    } else {
      const { data } = await api.post('/users/groups', { name: groupForm.name, description: groupForm.description })
      groupId = data.id
    }
    // Group policies live in a separate doc (za_user_groups.policies) — mfa_enforced
    // may only be changed by a genuine superadmin (server re-checks this too).
    await api.put(`/users/groups/${groupId}/policies`, {
      mfa_enforced: groupForm.mfa_enforced,
      setup_wizard: groupForm.setup_wizard,
      notifications_enabled: groupForm.notifications_enabled,
      single_session_enabled: groupForm.single_session_enabled,
      ip_restriction_enabled: groupForm.ip_restriction_enabled,
    })
    if (groupForm.notifications_enabled) {
      const emails = groupForm.notify_emails_text.split('\n').map(s => s.trim()).filter(Boolean)
      await api.put(`/users/groups/${groupId}/notify-config`, { emails, min_severity: groupForm.notify_min_severity })
    }
    if (groupForm.ip_restriction_enabled) {
      const cidrs = groupForm.ip_cidrs_text.split('\n').map(s => s.trim()).filter(Boolean)
      await api.put(`/users/groups/${groupId}/ip-restriction`, { cidrs })
    }
    closeGroupPanel()
    loadGroups()
  } catch (e: any) {
    groupError.value = e?.response?.data?.detail || 'Failed to save group'
  } finally {
    savingGroup.value = false
  }
}

// ── Inline group-roles expand (assign a role to everyone in the group) ─────
const expandedRolesGroupId = ref<string | null>(null)
const managingRolesGroup = ref<any>(null)
const checkedRoleIds = ref<Set<string>>(new Set())
const rolesError = ref('')
const savingRoles = ref(false)

async function toggleRoles(g: any) {
  closeMembers()
  if (expandedRolesGroupId.value === g.id) {
    closeRoles()
    return
  }
  expandedRolesGroupId.value = g.id
  managingRolesGroup.value = g
  rolesError.value = ''
  checkedRoleIds.value = new Set()
  try {
    const { data } = await api.get(`/users/groups/${g.id}/roles`)
    checkedRoleIds.value = new Set(data.role_ids || [])
  } catch {}
}

function toggleRole(roleId: string) {
  const s = new Set(checkedRoleIds.value)
  if (s.has(roleId)) s.delete(roleId)
  else s.add(roleId)
  checkedRoleIds.value = s
}

function closeRoles() {
  expandedRolesGroupId.value = null
  managingRolesGroup.value = null
  checkedRoleIds.value = new Set()
  rolesError.value = ''
}

async function saveRoles() {
  if (!managingRolesGroup.value) return
  savingRoles.value = true
  rolesError.value = ''
  try {
    await api.put(`/users/groups/${managingRolesGroup.value.id}/roles`, { role_ids: [...checkedRoleIds.value] })
    closeRoles()
    loadUsers()   // effective roles for members may have changed
  } catch (e: any) {
    rolesError.value = e?.response?.data?.detail || 'Failed to save roles'
  } finally {
    savingRoles.value = false
  }
}

// ── Inline members expand ──────────────────────────────────────────────────
const expandedGroupId = ref<string | null>(null)
const managingGroup = ref<any>(null)
const checkedMemberIds = ref<Set<string>>(new Set())
const memberSearch = ref('')
const membersError = ref('')
const savingMembers = ref(false)

const filteredMemberUsers = computed(() => {
  const q = memberSearch.value.trim().toLowerCase()
  return users.value.filter(u =>
    !q || u.username.toLowerCase().includes(q) || (u.display_name || '').toLowerCase().includes(q)
  )
})

async function toggleMembers(g: any) {
  closeRoles()
  if (expandedGroupId.value === g.id) {
    closeMembers()
    return
  }
  expandedGroupId.value = g.id
  managingGroup.value = g
  memberSearch.value = ''
  membersError.value = ''
  checkedMemberIds.value = new Set()
  try {
    const resp = await api.get(`/users/groups/${g.id}/members`)
    const ids = (resp.data || []).map((u: any) => u.id || u.user_id)
    checkedMemberIds.value = new Set(ids)
  } catch {}
}

function toggleMember(userId: string) {
  const s = new Set(checkedMemberIds.value)
  if (s.has(userId)) s.delete(userId)
  else s.add(userId)
  checkedMemberIds.value = s
}

function closeMembers() {
  expandedGroupId.value = null
  managingGroup.value = null
  checkedMemberIds.value = new Set()
  memberSearch.value = ''
}

async function searchGroups(query: string): Promise<PickerItem[]> {
  const selected = new Set(userGroupsPicker.value.map(p => p.id))
  const q = query.trim().toLowerCase()
  return groups.value
    .filter(g => !selected.has(g.id))
    .filter(g => !q || g.name.toLowerCase().includes(q))
    .slice(0, 20)
    .map(g => ({ id: g.id, label: g.name, sublabel: g.description }))
}

async function saveMembers() {
  if (!managingGroup.value) return
  savingMembers.value = true
  membersError.value = ''
  try {
    await api.put(`/users/groups/${managingGroup.value.id}/members`, {
      user_ids: [...checkedMemberIds.value],
    })
    closeMembers()
    loadGroups()
  } catch (e: any) {
    membersError.value = e?.response?.data?.detail || 'Failed to save members'
  } finally {
    savingMembers.value = false
  }
}

onMounted(() => { loadUsers(); loadGroups(); loadRoles() })
</script>

<style scoped>
.row-active td { background: rgba(88, 166, 255, 0.04); }

.fp-form { display: flex; flex-direction: column; gap: 10px; }
.fp-section-head {
  font-size: 10px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.08em; color: var(--text2); margin-top: 6px; padding-bottom: 4px;
  border-bottom: 1px solid var(--border);
}
.fp-field { display: flex; flex-direction: column; gap: 5px; }
.fp-label { font-size: 12px; color: var(--text2); font-weight: 500; }
.fp-input {
  padding: 7px 10px; background: var(--bg3); border: 1px solid var(--border);
  border-radius: 5px; color: var(--text); font-size: 13px; outline: none;
  width: 100%; box-sizing: border-box;
}
.fp-input:focus { border-color: var(--accent2); }
.fp-input:disabled { opacity: 0.5; cursor: not-allowed; }
.fp-error { font-size: 12px; color: var(--danger); padding: 4px 0; }
.fp-hint { font-size: 12px; color: var(--text2); background: var(--bg3); border: 1px solid var(--border); border-radius: 5px; padding: 8px 10px; }
.opt-row { display: flex; align-items: flex-start; gap: 8px; font-size: 12.5px; color: var(--text); padding: 3px 0; cursor: pointer; line-height: 1.5; }
.opt-row input { accent-color: var(--accent2); width: 15px; height: 15px; margin-top: 2px; flex-shrink: 0; }

.fp-toggle-group { display: flex; background: var(--bg3); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.fp-toggle {
  flex: 1; padding: 5px 0; font-size: 12px; font-weight: 500;
  background: transparent; border: none; color: var(--text2); cursor: pointer;
  transition: color 0.15s, background 0.15s;
}
.fp-toggle.active { background: var(--bg2); color: var(--text); }
.fp-toggle:hover:not(.active) { color: var(--text); }

/* Inline members expand */
.btn-pill-active {
  background: rgba(88,166,255,0.15); border-color: var(--accent2); color: var(--accent2);
}
.members-expand-row td { border-top: none !important; }
.members-expand {
  border-top: 1px solid var(--border);
  background: var(--bg3);
  padding: 0;
}
.members-expand-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px 6px;
}
.members-expand-title {
  font-size: 12px; font-weight: 600; color: var(--text2);
  text-transform: uppercase; letter-spacing: 0.06em;
}
.members-search {
  display: block; width: 100%; box-sizing: border-box;
  margin: 0; padding: 7px 14px;
  background: var(--bg3); border: none;
  border-top: 1px solid var(--border); border-bottom: 1px solid var(--border);
  color: var(--text); font-size: 13px; outline: none;
}
.members-search::placeholder { color: var(--text2); }
.members-search:focus { background: var(--bg2); }
.members-list {
  max-height: 220px; overflow-y: auto;
  padding: 4px 0;
}
.members-row {
  display: flex; align-items: center; gap: 10px;
  padding: 7px 14px; cursor: pointer;
  transition: background 0.1s;
  user-select: none;
}
.members-row:hover { background: var(--bg3); }
.members-row.checked { background: rgba(88,166,255,0.06); }
.members-checkbox {
  width: 15px; height: 15px; flex-shrink: 0;
  accent-color: var(--accent2); cursor: pointer;
}
.members-username { font-size: 13px; color: var(--text); font-weight: 500; }
.members-display { font-size: 12px; color: var(--text2); }
.members-expand-footer {
  display: flex; justify-content: flex-end; gap: 8px;
  padding: 10px 14px;
  border-top: 1px solid var(--border);
}

.fp-opt { font-size: 10px; font-weight: 400; color: var(--text2); }
.role-check-list { display: flex; flex-direction: column; gap: 6px; }
.role-check {
  display: grid; grid-template-columns: auto auto 1fr; align-items: center; gap: 8px;
  padding: 7px 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg3);
  cursor: pointer; transition: border-color 0.12s, background 0.12s;
}
.role-check:hover { border-color: var(--accent2); }
.role-check.checked { border-color: rgba(88,166,255,0.5); background: rgba(88,166,255,0.08); }
.role-check input { accent-color: var(--accent2); width: 15px; height: 15px; }
.role-check-name { font-size: 13px; font-weight: 600; color: var(--text); text-transform: capitalize; }
.role-check-desc { font-size: 11px; color: var(--text2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ── Source badges (Zabbix vs native) ───────────────────────────────────── */
.src-badge {
  display: inline-flex; align-items: center; justify-content: center;
  width: 16px; height: 16px; border-radius: 3px; margin-right: 6px;
  font-size: 9px; font-weight: 800; line-height: 1; flex-shrink: 0;
}
.src-badge--zbx { background: rgba(240,136,62,0.15); border: 1px solid rgba(240,136,62,0.5); color: #f0883e; }
.src-badge--sr  { background: rgba(88,166,255,0.12); border: 1px solid rgba(88,166,255,0.35); color: var(--accent2); }

/* ── New-accounts-from-sync modal ───────────────────────────────────────── */
.new-account-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 10px; border: 1px solid var(--border); border-radius: 6px; margin-bottom: 6px;
}
.new-account-user { font-size: 13px; font-weight: 600; color: var(--text); }
.new-account-pw { font-size: 13px; color: #f0883e; background: var(--bg3); padding: 2px 8px; border-radius: 4px; }

/* ── Group roles expand (mirrors the members expand pattern) ───────────── */
.roles-expand { border-top: 1px solid var(--border); background: var(--bg3); padding: 12px 14px; }
.roles-expand-title { font-size: 12px; font-weight: 600; color: var(--text2); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 10px; }
.roles-expand-footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; }
</style>
