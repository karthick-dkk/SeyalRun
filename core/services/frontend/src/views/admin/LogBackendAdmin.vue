<template>
  <div>
    <div class="card" style="max-width:680px">
      <div class="card-header">Log Storage Backend</div>
      <div style="padding:18px">
        <div class="fp-field">
          <label class="fp-label">Backend</label>
          <div class="fp-toggle-group">
            <button v-for="b in backends" :key="b" :class="['fp-toggle', form.backend === b && 'active']" @click="form.backend = b">{{ labelFor(b) }}</button>
          </div>
        </div>

        <template v-if="form.backend === 'elasticsearch' || form.backend === 'es+s3'">
          <div class="fp-section-head">Elasticsearch</div>
          <div class="fp-field"><label class="fp-label">URL</label><input v-model="form.es_url" class="fp-input" placeholder="https://es.example.com:9200" /></div>
          <div class="fp-field"><label class="fp-label">API Key</label><input v-model="form.es_api_key" type="password" class="fp-input" placeholder="••••••••" /></div>
          <div class="fp-field"><label class="fp-label">Index Prefix</label><input v-model="form.es_index_prefix" class="fp-input" placeholder="seyalrun" /></div>
          <div class="fp-field"><label class="fp-label" style="display:flex;align-items:center;gap:8px;cursor:pointer"><input type="checkbox" v-model="form.es_verify_ssl" style="width:auto" /> Verify TLS certificate <span class="hint">(uncheck for a self-signed Elasticsearch)</span></label></div>
        </template>

        <template v-if="form.backend === 's3' || form.backend === 'es+s3'">
          <div class="fp-section-head">S3 / Object Storage</div>
          <div class="fp-field"><label class="fp-label">Bucket</label><input v-model="form.s3_bucket" class="fp-input" placeholder="seyalrun-logs" /></div>
          <div class="fp-field"><label class="fp-label">Region</label><input v-model="form.s3_region" class="fp-input" placeholder="us-east-1" /></div>
          <div class="fp-field"><label class="fp-label">Access Key ID</label><input v-model="form.s3_access_key_id" class="fp-input" /></div>
          <div class="fp-field"><label class="fp-label">Secret Access Key</label><input v-model="form.s3_secret_access_key" type="password" class="fp-input" placeholder="••••••••" /></div>
          <div class="fp-field"><label class="fp-label">Endpoint URL <span class="fp-opt">(MinIO/compatible; blank for AWS)</span></label><input v-model="form.s3_endpoint_url" class="fp-input" /></div>
        </template>

        <div v-if="form.backend === 'local'" style="font-size:13px;color:var(--text2);padding:8px 0">
          Logs are written to the local <code>/var/log/seyalrun</code> volume only. Choose Elasticsearch or S3 to ship them centrally.
        </div>

        <!-- Content routing: which log category goes to which backend(s) -->
        <div class="fp-section-head" style="margin-top:16px">Content Routing</div>
        <div style="font-size:12px;color:var(--text2);margin:4px 0 8px">Pick which backend(s) receive each log category. Combine any (local + S3 + ES).</div>
        <table class="lb-route">
          <thead><tr><th></th><th>Local</th><th>Elasticsearch</th><th>S3</th></tr></thead>
          <tbody>
            <tr v-for="cat in categories" :key="cat.k">
              <td class="lb-route-cat">{{ cat.label }}<div class="lb-route-hint">{{ cat.hint }}</div></td>
              <td><input type="checkbox" :checked="has(cat.k,'local')" @change="toggle(cat.k,'local')" /></td>
              <td><input type="checkbox" :checked="has(cat.k,'elasticsearch')" @change="toggle(cat.k,'elasticsearch')" /></td>
              <td><input type="checkbox" :checked="has(cat.k,'s3')" @change="toggle(cat.k,'s3')" /></td>
            </tr>
          </tbody>
        </table>

        <!-- Test result -->
        <div v-if="testResult" class="lb-test">
          <div v-for="(res, name) in testResult" :key="name" class="lb-test-row">
            <span class="lb-test-name">{{ name }}</span>
            <span v-if="res.ok" class="badge badge-green">● OK · {{ res.latency_ms }}ms</span>
            <span v-else class="badge badge-red" :title="res.error">● {{ res.error || 'failed' }}</span>
          </div>
        </div>

        <div v-if="error" class="fp-error">{{ error }}</div>

        <div style="display:flex;gap:8px;margin-top:16px">
          <button class="btn" :disabled="testing" @click="testConn">{{ testing ? 'Testing…' : 'Test Connection' }}</button>
          <button class="btn btn-primary" :disabled="saving" @click="save">{{ saving ? 'Saving…' : 'Save' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import api from '@/api/client'

const backends = ['local', 'elasticsearch', 's3', 'es+s3']
function labelFor(b: string) { return { local: 'Local', elasticsearch: 'Elasticsearch', s3: 'S3', 'es+s3': 'ES + S3' }[b] || b }

// Routing categories the shipper classifies log lines into.
const categories = [
  { k: 'command', label: 'Command logs', hint: 'shell commands run in sessions' },
  { k: 'audit', label: 'Audit logs', hint: 'who did what, when' },
  { k: 'recording', label: 'Session recordings', hint: 'full terminal replays' },
  { k: 'app', label: 'Application logs', hint: 'everything else' },
]

const form = reactive<any>({
  backend: 'local', es_url: '', es_api_key: '', es_index_prefix: 'seyalrun', es_verify_ssl: true,
  s3_bucket: '', s3_region: '', s3_access_key_id: '', s3_secret_access_key: '', s3_endpoint_url: '',
  routing: {},
})

function has(cat: string, backend: string): boolean {
  return Array.isArray(form.routing?.[cat]) && form.routing[cat].includes(backend)
}
function toggle(cat: string, backend: string) {
  if (!form.routing || typeof form.routing !== 'object') form.routing = {}
  const cur: string[] = Array.isArray(form.routing[cat]) ? [...form.routing[cat]] : []
  const i = cur.indexOf(backend)
  if (i >= 0) cur.splice(i, 1); else cur.push(backend)
  form.routing = { ...form.routing, [cat]: cur }
}
const testResult = ref<any>(null)
const testing = ref(false)
const saving = ref(false)
const error = ref('')

async function load() {
  try {
    const { data } = await api.get('/log-backend')
    Object.assign(form, data)
    // First time (no routing saved yet): seed the layout the admin asked for —
    // command+audit to Elasticsearch, recordings to S3, everything else local.
    if (!form.routing || Object.keys(form.routing).length === 0) {
      form.routing = { command: ['elasticsearch'], audit: ['elasticsearch'], recording: ['s3'], app: ['local'] }
    }
  } catch (e: any) { error.value = e?.response?.data?.detail || 'Failed to load config' }
}
async function save() {
  saving.value = true; error.value = ''
  try {
    const payload = { ...form }
    // Don't resend masked secrets unchanged.
    if (payload.es_api_key === '••••••••') delete payload.es_api_key
    if (payload.s3_secret_access_key === '••••••••') delete payload.s3_secret_access_key
    const { data } = await api.put('/log-backend', payload)
    Object.assign(form, data)
  } catch (e: any) { error.value = e?.response?.data?.detail || 'Failed to save' }
  finally { saving.value = false }
}
async function testConn() {
  testing.value = true; error.value = ''; testResult.value = null
  try {
    // Save first so the test uses current values.
    await save()
    const { data } = await api.post('/log-backend/test')
    testResult.value = data
  } catch (e: any) { error.value = e?.response?.data?.detail || 'Test failed' }
  finally { testing.value = false }
}

onMounted(load)
</script>

<style scoped>
.fp-section-head { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text2); margin-top: 14px; padding-bottom: 4px; border-bottom: 1px solid var(--border); }
.fp-opt { font-size: 10px; font-weight: 400; text-transform: none; }
.fp-field { display: flex; flex-direction: column; gap: 5px; margin-top: 10px; }
.fp-label { font-size: 12px; color: var(--text2); font-weight: 500; }
.fp-input { padding: 7px 10px; background: var(--bg3); border: 1px solid var(--border); border-radius: 5px; color: var(--text); font-size: 13px; outline: none; width: 100%; box-sizing: border-box; }
.fp-input:focus { border-color: var(--accent2); }
.fp-error { font-size: 12px; color: var(--danger); padding: 8px 0; }
.fp-toggle-group { display: flex; background: var(--bg3); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.fp-toggle { flex: 1; padding: 7px 0; font-size: 12px; font-weight: 500; background: transparent; border: none; color: var(--text2); cursor: pointer; }
.fp-toggle.active { background: var(--bg2); color: var(--text); }
.lb-test { margin-top: 14px; display: flex; flex-direction: column; gap: 6px; }
.lb-test-row { display: flex; align-items: center; gap: 10px; }
.lb-test-name { font-size: 12px; color: var(--text2); text-transform: capitalize; min-width: 110px; }
.lb-route { width: 100%; border-collapse: collapse; margin-top: 8px; }
.lb-route th { font-size: 11px; font-weight: 600; color: var(--text2); text-align: center; padding: 4px 6px; }
.lb-route th:first-child { text-align: left; }
.lb-route td { padding: 6px; border-top: 1px solid var(--border); text-align: center; }
.lb-route td.lb-route-cat { text-align: left; font-size: 13px; color: var(--text); }
.lb-route-hint { font-size: 11px; color: var(--text2); font-weight: 400; }
.lb-route input[type=checkbox] { width: 16px; height: 16px; cursor: pointer; }
</style>
