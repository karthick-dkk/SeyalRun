<template>
  <div>
    <div class="card" style="max-width:680px">
      <div class="card-header">Mail Settings (MFA Email OTP)</div>
      <div style="padding:18px">
        <div style="font-size:12px;color:var(--text2);margin-bottom:12px">
          Used to send one-time codes for the "Email OTP" MFA method. Choose SMTP AUTH
          (e.g. Office 365 <code>smtp.office365.com:587</code>) or Microsoft Graph API
          (works even when a tenant has disabled legacy SMTP AUTH).
        </div>

        <div class="fp-field">
          <label class="fp-label" style="display:flex;align-items:center;gap:8px;cursor:pointer">
            <input type="checkbox" v-model="form.enabled" style="width:auto" /> Enable mail delivery
          </label>
        </div>

        <div class="fp-field">
          <label class="fp-label">Provider</label>
          <div class="fp-toggle-group">
            <button v-for="p in providers" :key="p" :class="['fp-toggle', form.provider === p && 'active']" @click="form.provider = p">{{ labelFor(p) }}</button>
          </div>
        </div>

        <div class="fp-section-head">Sender</div>
        <div class="fp-field"><label class="fp-label">From Address</label><input v-model="form.from_address" class="fp-input" placeholder="noreply@example.com" /></div>
        <div class="fp-field"><label class="fp-label">From Name</label><input v-model="form.from_name" class="fp-input" placeholder="SeyalRun" /></div>

        <template v-if="form.provider === 'smtp'">
          <div class="fp-section-head">SMTP</div>
          <div class="fp-field"><label class="fp-label">Host</label><input v-model="form.smtp_host" class="fp-input" placeholder="smtp.office365.com" /></div>
          <div class="fp-field"><label class="fp-label">Port</label><input v-model.number="form.smtp_port" type="number" class="fp-input" placeholder="587" /></div>
          <div class="fp-field"><label class="fp-label">Username</label><input v-model="form.smtp_username" class="fp-input" /></div>
          <div class="fp-field"><label class="fp-label">Password</label><input v-model="form.smtp_password" type="password" class="fp-input" placeholder="••••••••" /></div>
          <div class="fp-field"><label class="fp-label" style="display:flex;align-items:center;gap:8px;cursor:pointer"><input type="checkbox" v-model="form.smtp_use_tls" style="width:auto" /> Use STARTTLS</label></div>
        </template>

        <template v-if="form.provider === 'graph'">
          <div class="fp-section-head">Microsoft Graph</div>
          <div class="fp-field"><label class="fp-label">Tenant ID</label><input v-model="form.graph_tenant_id" class="fp-input" /></div>
          <div class="fp-field"><label class="fp-label">Client (App) ID</label><input v-model="form.graph_client_id" class="fp-input" /></div>
          <div class="fp-field"><label class="fp-label">Client Secret</label><input v-model="form.graph_client_secret" type="password" class="fp-input" placeholder="••••••••" /></div>
          <div class="fp-field"><label class="fp-label">Sender UPN</label><input v-model="form.graph_sender_upn" class="fp-input" placeholder="noreply@example.onmicrosoft.com" /></div>
        </template>

        <div class="fp-field" style="margin-top:14px">
          <label class="fp-label">Send Test Email To</label>
          <input v-model="testTo" class="fp-input" placeholder="you@example.com" />
        </div>

        <div v-if="testResult" class="lb-test">
          <span v-if="testResult.ok" class="badge badge-green">● Sent · {{ testResult.latency_ms }}ms</span>
          <span v-else class="badge badge-red" :title="testResult.error">● {{ testResult.error || 'failed' }}</span>
        </div>

        <div v-if="error" class="fp-error">{{ error }}</div>

        <div style="display:flex;gap:8px;margin-top:16px">
          <button class="btn" :disabled="testing || !testTo" @click="testMail">{{ testing ? 'Sending…' : 'Send Test Email' }}</button>
          <button class="btn btn-primary" :disabled="saving" @click="save">{{ saving ? 'Saving…' : 'Save' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import api from '@/api/client'

const providers = ['smtp', 'graph']
function labelFor(p: string) { return { smtp: 'SMTP (O365 / other)', graph: 'Microsoft Graph API' }[p] || p }

const form = reactive<any>({
  provider: 'smtp', enabled: false, from_address: '', from_name: '',
  smtp_host: '', smtp_port: 587, smtp_username: '', smtp_password: '', smtp_use_tls: true,
  graph_tenant_id: '', graph_client_id: '', graph_client_secret: '', graph_sender_upn: '',
})

const testTo = ref('')
const testResult = ref<any>(null)
const testing = ref(false)
const saving = ref(false)
const error = ref('')

async function load() {
  try {
    const { data } = await api.get('/settings/mail')
    Object.assign(form, data)
  } catch (e: any) { error.value = e?.response?.data?.detail || 'Failed to load config' }
}
async function save() {
  saving.value = true; error.value = ''
  try {
    const payload = { ...form }
    if (payload.smtp_password === '••••••••') delete payload.smtp_password
    if (payload.graph_client_secret === '••••••••') delete payload.graph_client_secret
    const { data } = await api.put('/settings/mail', payload)
    Object.assign(form, data)
  } catch (e: any) { error.value = e?.response?.data?.detail || 'Failed to save' }
  finally { saving.value = false }
}
async function testMail() {
  testing.value = true; error.value = ''; testResult.value = null
  try {
    await save()
    const { data } = await api.post('/settings/mail/test', { to: testTo.value })
    testResult.value = data
  } catch (e: any) { error.value = e?.response?.data?.detail || 'Test failed' }
  finally { testing.value = false }
}

onMounted(load)
</script>

<style scoped>
.fp-section-head { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text2); margin-top: 14px; padding-bottom: 4px; border-bottom: 1px solid var(--border); }
.fp-field { display: flex; flex-direction: column; gap: 5px; margin-top: 10px; }
.fp-label { font-size: 12px; color: var(--text2); font-weight: 500; }
.fp-input { padding: 7px 10px; background: var(--bg3); border: 1px solid var(--border); border-radius: 5px; color: var(--text); font-size: 13px; outline: none; width: 100%; box-sizing: border-box; }
.fp-input:focus { border-color: var(--accent2); }
.fp-error { font-size: 12px; color: var(--danger); padding: 8px 0; }
.fp-toggle-group { display: flex; background: var(--bg3); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.fp-toggle { flex: 1; padding: 7px 0; font-size: 12px; font-weight: 500; background: transparent; border: none; color: var(--text2); cursor: pointer; }
.fp-toggle.active { background: var(--bg2); color: var(--text); }
.lb-test { margin-top: 14px; }
</style>
