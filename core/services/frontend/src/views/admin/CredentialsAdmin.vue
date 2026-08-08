<template>
  <div>
    <!-- Credentials card -->
    <div class="card" style="margin-bottom:20px">
      <div class="card-header">
        Credentials
        <button class="btn btn-primary btn-sm" @click="openCreateCred">+ Credential</button>
      </div>
      <table class="table">
        <thead>
          <tr><th>Name</th><th>Username</th><th>Type</th><th>Strength</th><th>Scope</th><th>Flags</th><th>Hosts</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="c in credentials" :key="c.id" :class="{ 'row-active': activeCredId === c.id }">
            <td style="font-weight:600">{{ c.name || '—' }}</td>
            <td>{{ c.username }}</td>
            <td><span class="badge badge-blue">{{ c.secret_type }}</span></td>
            <td>
              <span v-if="c.strength_score != null" class="badge" :class="strengthClass(c.strength_score)">{{ strengthLabel(c.strength_score) }}</span>
              <span v-else style="color:var(--text2)">—</span>
            </td>
            <td style="color:var(--text2)">{{ c.credential_scope }}</td>
            <td>
              <span v-if="c.is_default" class="badge badge-green">Default</span>
              <span v-if="c.is_push_account" class="badge badge-orange" title="Push account for its linked hosts">Push</span>
              <span v-if="c.is_sudo" class="badge badge-blue" title="May escalate via sudo">sudo</span>
              <span v-if="!c.is_default && !c.is_push_account && !c.is_sudo" style="color:var(--text2)">—</span>
            </td>
            <td>
              <span v-if="c.host_ids?.length" class="badge badge-gray">{{ c.host_ids.length }} host{{ c.host_ids.length > 1 ? 's' : '' }}</span>
              <span v-else style="color:var(--text2)">—</span>
            </td>
            <td>
              <div style="display:flex;gap:6px;justify-content:flex-end;flex-wrap:wrap">
                <button class="btn-pill btn-pill-outline" @click="openReveal(c)">👁 View</button>
                <button class="btn-pill btn-pill-outline" @click="openRotation(c)">↻ Rotate</button>
                <button class="btn-pill btn-pill-outline" @click="openEditCred(c)">✎ Edit</button>
                <button class="btn-pill btn-pill-outline" style="color:var(--danger);border-color:var(--danger)" @click="removeCred(c)">&#128465; Delete</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!credentials.length && !loading" style="padding:32px;text-align:center;color:var(--text2)">No credentials yet.</div>
      <div v-if="loading" style="padding:32px;text-align:center;color:var(--text2)">Loading…</div>
    </div>

    <!-- Templates card -->
    <div class="card">
      <div class="card-header">
        Credential Templates
        <button class="btn btn-primary btn-sm" @click="openCreateTemplate">+ Template</button>
      </div>
      <table class="table">
        <thead>
          <tr><th>Name</th><th>Type</th><th>Default Username</th><th>Push</th><th>Rotation (days)</th><th>Description</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="t in templates" :key="t.id" :class="{ 'row-active': activeTemplateId === t.id }">
            <td style="font-weight:600">{{ t.name }}</td>
            <td><span class="badge badge-blue">{{ t.secret_type }}</span></td>
            <td style="color:var(--text2)">{{ t.default_username || '—' }}</td>
            <td>
              <span v-if="t.push_enabled" class="badge badge-green">Enabled</span>
              <span v-else style="color:var(--text2)">—</span>
            </td>
            <td style="color:var(--text2)">{{ t.rotation_days ?? '—' }}</td>
            <td style="color:var(--text2);font-size:12px">{{ t.description || '—' }}</td>
            <td>
              <div style="display:flex;gap:8px;justify-content:flex-end">
                <button class="btn-pill btn-pill-outline" @click="openEditTemplate(t)">✎ Edit</button>
                <button class="btn-pill btn-pill-outline" style="color:var(--danger);border-color:var(--danger)" @click="removeTemplate(t)">&#128465; Delete</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!templates.length && !loadingTemplates" style="padding:32px;text-align:center;color:var(--text2)">No credential templates yet.</div>
      <div v-if="loadingTemplates" style="padding:32px;text-align:center;color:var(--text2)">Loading…</div>
    </div>

    <!-- ── Credential panel ──────────────────────────────────────────────── -->
    <SlidePanel
      v-model="showCredPanel"
      :title="editingCred ? 'Edit Credential' : 'Add Credential'"
      :subtitle="editingCred ? (editingCred.name || editingCred.username) : undefined"
      :width="460"
      @close="closeCredPanel"
    >
      <div class="fp-form">
        <div class="fp-field">
          <label class="fp-label">Name</label>
          <input v-model="credForm.name" class="fp-input" placeholder="e.g. prod-db-svc" />
        </div>
        <div class="fp-field">
          <label class="fp-label">Username</label>
          <input v-model="credForm.username" class="fp-input" placeholder="e.g. svc_db" />
        </div>
        <div class="fp-field">
          <label class="fp-label">Template <span class="fp-opt">(optional)</span></label>
          <select v-model="credForm.template_id" class="fp-input">
            <option value="">— No Template —</option>
            <option v-for="t in templates" :key="t.id" :value="t.id">{{ t.name }}</option>
          </select>
        </div>

        <div class="fp-section-head">Secret</div>
        <div class="fp-field">
          <div class="fp-toggle-group">
            <button :class="['fp-toggle', credForm.secret_type === 'password' && 'active']" @click="credForm.secret_type = 'password'">Password</button>
            <button :class="['fp-toggle', credForm.secret_type === 'ssh_key' && 'active']" @click="credForm.secret_type = 'ssh_key'">SSH Key</button>
            <button :class="['fp-toggle', credForm.secret_type === 'vault_path' && 'active']" @click="credForm.secret_type = 'vault_path'">Vault</button>
          </div>
        </div>
        <div v-if="credForm.secret_type === 'password'" class="fp-field">
          <label class="fp-label">Password</label>
          <input v-model="credForm.password" type="password" class="fp-input" placeholder="••••••••" autocomplete="new-password" />
        </div>
        <template v-else-if="credForm.secret_type === 'ssh_key'">
          <div class="fp-field">
            <label class="fp-label">Private Key</label>
            <textarea v-model="credForm.private_key" class="fp-input fp-textarea" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" />
          </div>
          <div class="fp-field">
            <label class="fp-label">Passphrase <span class="fp-opt">(optional)</span></label>
            <input v-model="credForm.passphrase" type="password" class="fp-input" placeholder="••••••••" />
          </div>
        </template>
        <div v-else class="fp-field">
          <label class="fp-label">Vault Path</label>
          <input v-model="credForm.vault_path" class="fp-input" placeholder="secret/data/prod/db" />
        </div>
        <div class="fp-hint">
          <span v-if="editingCred">Leave blank to keep the current secret unchanged.</span>
          <span v-else>Secret is write-only and stored encrypted.</span>
        </div>

        <div class="fp-section-head">Linked Hosts</div>
        <div class="fp-field">
          <AsyncPicker v-model="credForm.hosts" :search-fn="searchHosts" :multiple="true" placeholder="Search and select hosts…" />
        </div>

        <div class="fp-section-head">Options</div>
        <div class="fp-field">
          <label class="fp-label">Scope</label>
          <div class="fp-toggle-group">
            <button :class="['fp-toggle', credForm.credential_scope === 'host' && 'active']" @click="credForm.credential_scope = 'host'">Host</button>
            <button :class="['fp-toggle', credForm.credential_scope === 'template' && 'active']" @click="credForm.credential_scope = 'template'">Template</button>
          </div>
        </div>
        <div class="fp-field">
          <label class="fp-checkbox">
            <input type="checkbox" v-model="credForm.is_default" />
            <span class="fp-checkbox-label">Default credential for its scope</span>
          </label>
        </div>
        <div class="fp-field">
          <label class="fp-checkbox">
            <input type="checkbox" v-model="credForm.is_sudo" />
            <span class="fp-checkbox-label">Sudo enabled <span style="color:var(--text2);font-weight:400">— may escalate (runs privileged commands via sudo)</span></span>
          </label>
        </div>
        <div class="fp-field">
          <label class="fp-checkbox">
            <input type="checkbox" v-model="credForm.is_push_account" />
            <span class="fp-checkbox-label">Push account <span style="color:var(--text2);font-weight:400">— use this account to create/manage users on its linked hosts</span></span>
          </label>
        </div>
        <div v-if="credForm.is_push_account && !credForm.is_sudo && credForm.username !== 'root'" style="font-size:11px;color:var(--warn,#d29922);margin:-4px 0 8px">
          ⚠ A non-root push account should also have <b>Sudo enabled</b>, otherwise account creation will fail with “Permission denied”.
        </div>

        <div v-if="credError" class="fp-error">{{ credError }}</div>
      </div>

      <template #footer>
        <button class="btn" @click="closeCredPanel">Cancel</button>
        <button class="btn btn-primary" @click="saveCred" :disabled="savingCred">{{ savingCred ? 'Saving…' : (editingCred ? 'Save' : 'Create') }}</button>
      </template>
    </SlidePanel>

    <!-- ── Template panel ───────────────────────────────────────────────── -->
    <SlidePanel
      v-model="showTemplatePanel"
      :title="editingTemplate ? 'Edit Template' : 'Add Credential Template'"
      :subtitle="editingTemplate?.name"
      :width="420"
      @close="closeTemplatePanel"
    >
      <div class="fp-form">
        <div class="fp-field">
          <label class="fp-label">Name</label>
          <input v-model="templateForm.name" class="fp-input" placeholder="e.g. linux-service-account" />
        </div>
        <div class="fp-field">
          <label class="fp-label">Secret Type</label>
          <div class="fp-toggle-group">
            <button :class="['fp-toggle', templateForm.secret_type === 'password' && 'active']" @click="templateForm.secret_type = 'password'">Password</button>
            <button :class="['fp-toggle', templateForm.secret_type === 'ssh_key' && 'active']" @click="templateForm.secret_type = 'ssh_key'">SSH Key</button>
            <button :class="['fp-toggle', templateForm.secret_type === 'vault_path' && 'active']" @click="templateForm.secret_type = 'vault_path'">Vault</button>
          </div>
        </div>
        <div class="fp-field">
          <label class="fp-label">Description</label>
          <input v-model="templateForm.description" class="fp-input" placeholder="Optional description" />
        </div>
        <div class="fp-field">
          <label class="fp-label">Default Username</label>
          <input v-model="templateForm.default_username" class="fp-input" placeholder="e.g. svc_default" />
        </div>
        <div class="fp-field">
          <label class="fp-checkbox">
            <input type="checkbox" v-model="templateForm.push_enabled" />
            <span class="fp-checkbox-label">Account push enabled</span>
          </label>
        </div>
        <div class="fp-field">
          <label class="fp-label">Rotation (days) <span class="fp-opt">(optional)</span></label>
          <input v-model.number="templateForm.rotation_days" type="number" min="1" class="fp-input" placeholder="e.g. 90" />
        </div>
        <div v-if="templateError" class="fp-error">{{ templateError }}</div>
      </div>
      <template #footer>
        <button class="btn" @click="closeTemplatePanel">Cancel</button>
        <button class="btn btn-primary" @click="saveTemplate" :disabled="savingTemplate">{{ savingTemplate ? 'Saving…' : (editingTemplate ? 'Save' : 'Create') }}</button>
      </template>
    </SlidePanel>

    <!-- ── Reveal Secret modal (MFA-gated, Feature 6) ─────────────────────── -->
    <div v-if="reveal.visible" class="modal-overlay" style="z-index:520" @click.self="closeReveal">
      <div class="modal" style="width:min(440px,94vw)">
        <div class="modal-header">View Secret — {{ reveal.cred?.name || reveal.cred?.username }}<button class="btn btn-sm btn-icon" @click="closeReveal">✕</button></div>
        <div class="modal-body">
          <!-- Step 1: MFA code (MFA is mandatory to reveal — see reveal.error if not enabled) -->
          <div v-if="reveal.needCode && !reveal.secret">
            <p v-if="reveal.mfaMethod === 'email'" style="font-size:13px;color:var(--text2);margin:0 0 10px">
              Enter the 6-digit code emailed to you.
              <button class="btn btn-sm" style="margin-left:6px" :disabled="reveal.busy" @click="sendRevealCode">{{ reveal.codeSent ? 'Resend' : 'Send code' }}</button>
            </p>
            <p v-else style="font-size:13px;color:var(--text2);margin:0 0 10px">Enter your 6-digit authenticator code to reveal this secret.</p>
            <input v-model="reveal.code" class="fp-input" inputmode="numeric" maxlength="6" placeholder="123456" style="text-align:center;letter-spacing:6px;font-size:18px" @keyup.enter="doReveal" />
          </div>
          <div v-else-if="!reveal.secret && !reveal.error" style="font-size:13px;color:var(--text2)">Revealing…</div>

          <!-- Step 2: revealed secret -->
          <div v-if="reveal.secret">
            <div v-for="(val, key) in reveal.secret" :key="key" class="reveal-row">
              <div class="reveal-key">{{ key }}</div>
              <code class="reveal-val" :class="{ blurred: reveal.blurred }" @click="reveal.blurred = false">{{ val }}</code>
            </div>
            <div style="font-size:11px;color:var(--text2);margin-top:8px">
              Auto-clears in {{ reveal.countdown }}s. Click a value to unblur.
            </div>
          </div>

          <div v-if="reveal.error" class="fp-error">{{ reveal.error }}</div>
        </div>
        <div class="modal-footer">
          <button class="btn" @click="closeReveal">Close</button>
          <button v-if="reveal.needCode && !reveal.secret" class="btn btn-primary" :disabled="reveal.busy || reveal.code.length < 6" @click="doReveal">{{ reveal.busy ? 'Verifying…' : 'Reveal' }}</button>
        </div>
      </div>
    </div>

    <!-- ── Rotation panel (Feature 10) ────────────────────────────────────── -->
    <SlidePanel
      v-model="showRotationPanel"
      title="Rotation"
      :subtitle="rotation.cred ? (rotation.cred.name || rotation.cred.username) : undefined"
      :width="420"
      @close="closeRotation"
    >
      <div class="fp-form">
        <div class="fp-section-head">Policy</div>
        <div class="fp-field">
          <label class="fp-label">Rotate every (days)</label>
          <input v-model.number="rotation.form.rotation_days" type="number" min="1" class="fp-input" />
        </div>
        <div class="fp-field">
          <label class="fp-label">Mode</label>
          <div class="fp-toggle-group">
            <button :class="['fp-toggle', rotation.form.rotation_mode === 'auto' && 'active']" @click="rotation.form.rotation_mode = 'auto'">Auto</button>
            <button :class="['fp-toggle', rotation.form.rotation_mode === 'manual' && 'active']" @click="rotation.form.rotation_mode = 'manual'">Manual</button>
          </div>
        </div>
        <div class="fp-field">
          <label class="fp-checkbox">
            <input type="checkbox" v-model="rotation.form.enabled" />
            <span class="fp-checkbox-label">Policy enabled</span>
          </label>
        </div>
        <div class="fp-field" style="flex-direction:row;gap:16px">
          <div><div class="fp-label">Last rotated</div><div style="font-size:12px">{{ fmtDate(rotation.policy?.last_rotated_at) }}</div></div>
          <div><div class="fp-label">Next due</div><div style="font-size:12px">{{ fmtDate(rotation.policy?.next_rotation_due) }}</div></div>
        </div>
        <button class="btn btn-sm" :disabled="rotation.savingPolicy" @click="saveRotationPolicy">{{ rotation.savingPolicy ? 'Saving…' : 'Save Policy' }}</button>

        <div class="fp-section-head" style="margin-top:14px">Manual Rotation</div>
        <p style="font-size:12px;color:var(--text2);margin:0">Generates a new secret on the target hosts via an Account/Rotate job and archives the old one.</p>
        <button class="btn btn-sm btn-primary" :disabled="rotation.rotating" @click="doRotate">{{ rotation.rotating ? 'Dispatching…' : '↻ Rotate Now' }}</button>

        <div class="fp-section-head" style="margin-top:14px">History (last 5)</div>
        <div v-if="rotation.history.length" class="rot-history">
          <div v-for="h in rotation.history.slice(0,5)" :key="h.id" class="rot-history-row">
            <span>{{ fmtDate(h.rotated_at) }}</span>
            <span style="color:var(--text2)">{{ h.rotated_by ? 'by user' : 'system' }}</span>
          </div>
        </div>
        <div v-else style="font-size:12px;color:var(--text2)">No rotations recorded yet.</div>

        <div v-if="rotation.error" class="fp-error">{{ rotation.error }}</div>
      </div>
      <template #footer>
        <button class="btn" @click="closeRotation">Close</button>
      </template>
    </SlidePanel>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import AsyncPicker, { type PickerItem } from '@/components/common/AsyncPicker.vue'
import SlidePanel from '@/components/common/SlidePanel.vue'
import api from '@/api/client'
import { useConfirm } from '@/composables/useConfirm'

const { confirm } = useConfirm()

const credentials = ref<any[]>([])
const templates = ref<any[]>([])
const hosts = ref<any[]>([])
const loading = ref(false)
const loadingTemplates = ref(false)

const templateById = computed(() => new Map(templates.value.map(t => [t.id, t])))
function templateName(id: string | null) {
  if (!id) return '—'
  return templateById.value.get(id)?.name ?? id
}

async function loadCredentials() {
  loading.value = true
  try { const { data } = await api.get('/credentials'); credentials.value = data }
  finally { loading.value = false }
}
async function loadTemplates() {
  loadingTemplates.value = true
  try { const { data } = await api.get('/credential-templates'); templates.value = data }
  finally { loadingTemplates.value = false }
}
async function loadHosts() {
  try { const { data } = await api.get('/hosts'); hosts.value = data } catch {}
}

// ── Credential panel ───────────────────────────────────────────────────────
const showCredPanel = ref(false)
const editingCred = ref<any>(null)
const activeCredId = ref<string | null>(null)
const credForm = reactive({
  name: '', username: '', template_id: '', secret_type: 'password',
  password: '', private_key: '', passphrase: '', vault_path: '',
  credential_scope: 'host', is_default: false, is_sudo: false, is_push_account: false,
  hosts: [] as PickerItem[],
})
const credError = ref('')
const savingCred = ref(false)

function openCreateCred() {
  editingCred.value = null
  activeCredId.value = null
  Object.assign(credForm, {
    name: '', username: '', template_id: '', secret_type: 'password',
    password: '', private_key: '', passphrase: '', vault_path: '',
    credential_scope: 'host', is_default: false, is_sudo: false, is_push_account: false, hosts: [],
  })
  credError.value = ''
  showCredPanel.value = true
}

function openEditCred(c: any) {
  editingCred.value = c
  activeCredId.value = c.id
  const hostById = new Map(hosts.value.map(h => [h.id, h]))
  Object.assign(credForm, {
    name: c.name || '', username: c.username, template_id: c.template_id || '', secret_type: c.secret_type,
    password: '', private_key: '', passphrase: '', vault_path: '',
    credential_scope: c.credential_scope, is_default: c.is_default,
    is_sudo: !!c.is_sudo, is_push_account: !!c.is_push_account,
    hosts: (c.host_ids || []).map((id: string) => ({ id, label: hostById.get(id)?.name || id, sublabel: hostById.get(id)?.ip })),
  })
  credError.value = ''
  showCredPanel.value = true
}

function closeCredPanel() { showCredPanel.value = false; editingCred.value = null; activeCredId.value = null }

function buildSecret(isEdit: boolean): Record<string, any> | null {
  if (credForm.secret_type === 'password') {
    if (!credForm.password) {
      if (!isEdit) { credError.value = 'Password is required'; return null }
      return {}  // keep existing
    }
    return { password: credForm.password }
  }
  if (credForm.secret_type === 'ssh_key') {
    if (!credForm.private_key) {
      if (!isEdit) { credError.value = 'Private key is required'; return null }
      return {}  // keep existing
    }
    const s: Record<string, any> = { private_key: credForm.private_key }
    if (credForm.passphrase) s.passphrase = credForm.passphrase
    return s
  }
  if (!credForm.vault_path) {
    if (!isEdit) { credError.value = 'Vault path is required'; return null }
    return {}  // keep existing
  }
  return { path: credForm.vault_path }
}

async function saveCred() {
  credError.value = ''
  const isEdit = !!editingCred.value
  const secret = buildSecret(isEdit)
  if (secret === null) return
  savingCred.value = true
  try {
    const payload = {
      name: credForm.name,
      template_id: credForm.template_id || null,
      username: credForm.username,
      secret_type: credForm.secret_type,
      secret,
      credential_scope: credForm.credential_scope,
      is_default: credForm.is_default,
      is_sudo: credForm.is_sudo,
      is_push_account: credForm.is_push_account,
      host_ids: credForm.hosts.map(h => h.id),
    }
    if (editingCred.value) {
      await api.put(`/credentials/${editingCred.value.id}`, payload)
    } else {
      await api.post('/credentials', payload)
    }
    closeCredPanel()
    loadCredentials()
  } catch (e: any) {
    credError.value = e?.response?.data?.detail || 'Failed to save credential'
  } finally {
    savingCred.value = false
  }
}

async function removeCred(c: any) {
  if (!await confirm(`Delete credential "${c.name || c.username}"?`, { title: 'Delete Credential', danger: true, confirmLabel: 'Delete' })) return
  try {
    await api.delete(`/credentials/${c.id}`)
    loadCredentials()
  } catch (e: any) {
    alert(e?.response?.data?.detail || 'Failed to delete credential')
  }
}

async function searchHosts(query: string): Promise<PickerItem[]> {
  const selected = new Set(credForm.hosts.map(h => h.id))
  const q = query.trim().toLowerCase()
  return hosts.value
    .filter(h => !selected.has(h.id))
    .filter(h => !q || h.name.toLowerCase().includes(q) || h.ip.includes(q))
    .slice(0, 20)
    .map(h => ({ id: h.id, label: h.name, sublabel: h.ip }))
}

// ── Template panel ─────────────────────────────────────────────────────────
const showTemplatePanel = ref(false)
const editingTemplate = ref<any>(null)
const activeTemplateId = ref<string | null>(null)
const templateForm = reactive({
  name: '', secret_type: 'password', description: '', default_username: '',
  push_enabled: false, rotation_days: null as number | null,
})
const templateError = ref('')
const savingTemplate = ref(false)

function openCreateTemplate() {
  editingTemplate.value = null
  activeTemplateId.value = null
  Object.assign(templateForm, { name: '', secret_type: 'password', description: '', default_username: '', push_enabled: false, rotation_days: null })
  templateError.value = ''
  showTemplatePanel.value = true
}

function openEditTemplate(t: any) {
  editingTemplate.value = t
  activeTemplateId.value = t.id
  Object.assign(templateForm, {
    name: t.name, secret_type: t.secret_type, description: t.description || '',
    default_username: t.default_username || '', push_enabled: t.push_enabled, rotation_days: t.rotation_days,
  })
  templateError.value = ''
  showTemplatePanel.value = true
}

function closeTemplatePanel() { showTemplatePanel.value = false; editingTemplate.value = null; activeTemplateId.value = null }

async function saveTemplate() {
  savingTemplate.value = true
  templateError.value = ''
  try {
    const payload = {
      name: templateForm.name,
      secret_type: templateForm.secret_type,
      description: templateForm.description,
      default_username: templateForm.default_username,
      default_params: editingTemplate.value?.default_params || {},
      push_enabled: templateForm.push_enabled,
      rotation_days: templateForm.rotation_days || null,
    }
    if (editingTemplate.value) {
      await api.put(`/credential-templates/${editingTemplate.value.id}`, payload)
    } else {
      await api.post('/credential-templates', payload)
    }
    closeTemplatePanel()
    loadTemplates()
  } catch (e: any) {
    templateError.value = e?.response?.data?.detail || 'Failed to save template'
  } finally {
    savingTemplate.value = false
  }
}

async function removeTemplate(t: any) {
  if (!await confirm(`Delete credential template "${t.name}"?`, { title: 'Delete Template', danger: true, confirmLabel: 'Delete' })) return
  try {
    await api.delete(`/credential-templates/${t.id}`)
    loadTemplates()
  } catch (e: any) {
    alert(e?.response?.data?.detail || 'Failed to delete template')
  }
}

// ── Password strength (Feature 9) ───────────────────────────────────────────
function strengthLabel(s: number): string {
  return ['Weak', 'Weak', 'Fair', 'Strong', 'Very Strong'][s] ?? '—'
}
function strengthClass(s: number): string {
  return ['badge-red', 'badge-red', 'badge-yellow', 'badge-green', 'badge-blue'][s] ?? 'badge-gray'
}

function fmtDate(iso?: string | null): string {
  return iso ? new Date(iso).toLocaleString() : '—'
}

// ── Reveal secret (MFA-mandatory — see auth.py::mfa_verify) ─────────────────
const reveal = reactive<any>({
  visible: false, cred: null, needCode: false, mfaMethod: '', codeSent: false, code: '', secret: null,
  blurred: true, countdown: 30, busy: false, error: '', timer: null as any,
})
async function openReveal(c: any) {
  Object.assign(reveal, {
    visible: true, cred: c, needCode: false, mfaMethod: '', codeSent: false,
    code: '', secret: null, blurred: true, countdown: 30, busy: false, error: '',
  })
  let enabled = false
  try {
    const { data } = await api.get('/auth/mfa/status')
    enabled = !!data.enabled
    reveal.mfaMethod = data.method || ''
  } catch { /* treat as not enabled */ }
  if (!enabled) {
    reveal.error = 'Enable MFA in Security settings to reveal credentials.'
    return
  }
  reveal.needCode = true
  if (reveal.mfaMethod === 'email') await sendRevealCode()
}
async function sendRevealCode() {
  reveal.busy = true; reveal.error = ''
  try {
    await api.post('/auth/mfa/reveal/request-code')
    reveal.codeSent = true
  } catch (e: any) {
    reveal.error = e?.response?.data?.detail || 'Failed to send code'
  } finally {
    reveal.busy = false
  }
}
async function doReveal() {
  reveal.busy = true; reveal.error = ''
  try {
    // reveal token is bound to this specific credential + the current user
    const verify = await api.post('/auth/mfa/verify', { totp_code: reveal.code, credential_id: reveal.cred.id })
    const token = verify.data.reveal_token
    const { data } = await api.get(`/credentials/${reveal.cred.id}/reveal`, { headers: { 'X-Reveal-Token': token } })
    reveal.secret = data.secret
    reveal.blurred = true
    reveal.countdown = 30
    if (reveal.timer) clearInterval(reveal.timer)
    reveal.timer = setInterval(() => {
      reveal.countdown -= 1
      if (reveal.countdown <= 0) { reveal.secret = null; reveal.blurred = true; clearInterval(reveal.timer); reveal.timer = null }
    }, 1000)
  } catch (e: any) {
    reveal.error = e?.response?.data?.detail || 'Failed to reveal secret'
  } finally {
    reveal.busy = false
  }
}
function closeReveal() {
  if (reveal.timer) clearInterval(reveal.timer)
  Object.assign(reveal, { visible: false, cred: null, secret: null, code: '', error: '', timer: null })
}

// ── Rotation (Feature 10) ───────────────────────────────────────────────────
const showRotationPanel = ref(false)
const rotation = reactive<any>({
  cred: null, policy: null, history: [] as any[],
  form: { rotation_days: 90, rotation_mode: 'auto', enabled: false },
  savingPolicy: false, rotating: false, error: '',
})
async function openRotation(c: any) {
  rotation.cred = c; rotation.error = ''; rotation.history = []; rotation.policy = null
  showRotationPanel.value = true
  try {
    const [p, h] = await Promise.all([
      api.get(`/credentials/${c.id}/rotation-policy`),
      api.get(`/credentials/${c.id}/history`),
    ])
    rotation.policy = p.data
    rotation.form = { rotation_days: p.data.rotation_days || 90, rotation_mode: p.data.rotation_mode || 'auto', enabled: !!p.data.enabled }
    rotation.history = h.data
  } catch (e: any) {
    rotation.error = e?.response?.data?.detail || 'Failed to load rotation policy'
  }
}
function closeRotation() { showRotationPanel.value = false; rotation.cred = null }
async function saveRotationPolicy() {
  rotation.savingPolicy = true; rotation.error = ''
  try {
    const { data } = await api.put(`/credentials/${rotation.cred.id}/rotation-policy`, rotation.form)
    rotation.policy = data
  } catch (e: any) {
    rotation.error = e?.response?.data?.detail || 'Failed to save policy'
  } finally { rotation.savingPolicy = false }
}
async function doRotate() {
  rotation.rotating = true; rotation.error = ''
  try {
    await api.post(`/credentials/${rotation.cred.id}/rotate`, {})
    const h = await api.get(`/credentials/${rotation.cred.id}/history`)
    rotation.history = h.data
    loadCredentials()
  } catch (e: any) {
    rotation.error = e?.response?.data?.detail || 'Rotation failed (configure a rotate_secret template in Automation)'
  } finally { rotation.rotating = false }
}

onMounted(() => { loadCredentials(); loadTemplates(); loadHosts() })
</script>

<style scoped>
.row-active td { background: rgba(88, 166, 255, 0.04); }

.fp-form { display: flex; flex-direction: column; gap: 10px; }
.fp-section-head {
  font-size: 10px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.08em; color: var(--text2); margin-top: 6px; padding-bottom: 4px;
  border-bottom: 1px solid var(--border);
}
.fp-opt { font-size: 10px; font-weight: 400; text-transform: none; letter-spacing: 0; }
.fp-field { display: flex; flex-direction: column; gap: 5px; }
.fp-label { font-size: 12px; color: var(--text2); font-weight: 500; }
.fp-input {
  padding: 7px 10px; background: var(--bg3); border: 1px solid var(--border);
  border-radius: 5px; color: var(--text); font-size: 13px; outline: none;
  width: 100%; box-sizing: border-box;
}
.fp-input:focus { border-color: var(--accent2); }
.fp-textarea { resize: vertical; min-height: 100px; font-family: var(--font-mono, monospace); font-size: 11px; }
.fp-error { font-size: 12px; color: var(--danger); padding: 4px 0; }
.fp-hint { font-size: 11px; color: var(--text2); }

.fp-toggle-group { display: flex; background: var(--bg3); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.fp-toggle {
  flex: 1; padding: 5px 0; font-size: 12px; font-weight: 500;
  background: transparent; border: none; color: var(--text2); cursor: pointer;
  transition: color 0.15s, background 0.15s;
}
.fp-toggle.active { background: var(--bg2); color: var(--text); }
.fp-toggle:hover:not(.active) { color: var(--text); }

.fp-checkbox { display: flex; align-items: center; gap: 6px; cursor: pointer; }
.fp-checkbox input[type="checkbox"] { width: 14px; height: 14px; accent-color: var(--accent2); }
.fp-checkbox-label { font-size: 12px; color: var(--text); }

/* Reveal secret */
.reveal-row { display: flex; flex-direction: column; gap: 3px; margin-bottom: 10px; }
.reveal-key { font-size: 11px; color: var(--text2); text-transform: uppercase; letter-spacing: 0.05em; }
.reveal-val {
  display: block; background: var(--bg3); border: 1px solid var(--border); border-radius: 6px;
  padding: 9px 11px; font-family: var(--font-mono, monospace); font-size: 12px; color: var(--text);
  word-break: break-all; white-space: pre-wrap; cursor: pointer; transition: filter 0.15s;
}
.reveal-val.blurred { filter: blur(5px); }

/* Rotation history */
.rot-history { display: flex; flex-direction: column; gap: 4px; }
.rot-history-row { display: flex; justify-content: space-between; font-size: 12px; padding: 5px 0; border-bottom: 1px solid var(--border); }
</style>
