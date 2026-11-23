<template>
  <AppShell>
    <div class="page">
      <div class="ph">
        <div class="row" style="gap:14px">
          <span class="b info nd" style="width:44px;height:44px;padding:0;justify-content:center;border-radius:10px">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z" /></svg>
          </span>
          <div>
            <h1 class="h1">MCP for AI agents</h1>
            <p class="sub">Let AI agents such as Claude work with SeyalRun over the Model Context Protocol. An agent can only do what its token's scopes and its owner's role both allow.</p>
          </div>
        </div>
        <div class="acts">
          <router-link v-if="auth.can('admin.audit')" class="btn" :to="AUDIT_LINK">Tool-call audit</router-link>
          <router-link v-if="auth.can('admin.security')" class="btn p" to="/settings/security">New token</router-link>
        </div>
      </div>

      <div class="stack">
        <div class="grid2" style="align-items:start">
          <section class="card">
            <div class="ch"><h2 class="ct">Connect an agent</h2></div>
            <div class="cb" style="display:flex;flex-direction:column;gap:14px">
              <dl class="kv">
                <dt>Endpoint</dt>
                <dd>
                  <span class="row" style="gap:6px">
                    <span class="mono" style="overflow-wrap:anywhere">{{ endpoint }}</span>
                    <button class="btn sm ib g" type="button" :aria-label="copied === 'ep' ? 'Copied' : 'Copy endpoint'" :title="copied === 'ep' ? 'Copied' : 'Copy endpoint'" @click="copy(endpoint, 'ep')">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></svg>
                    </button>
                    <span v-if="copied === 'ep'" class="muted" style="font-size:12.5px" role="status">Copied</span>
                  </span>
                </dd>
                <dt>Transport</dt><dd>Streamable HTTP · JSON-RPC 2.0</dd>
                <dt>Sign-in</dt><dd>Personal access token as <span class="mono">Authorization: Bearer sr_…</span></dd>
                <dt>Discovery</dt><dd class="mono" style="font-size:12.5px;overflow-wrap:anywhere">/.well-known/oauth-protected-resource</dd>
              </dl>
              <div>
                <div class="row" style="justify-content:space-between;margin-bottom:6px"><span class="lbl" style="margin:0">Client config</span>
                  <button class="btn sm g" type="button" @click="copy(clientConfig, 'cfg')">{{ copied === 'cfg' ? 'Copied' : 'Copy' }}</button></div>
                <pre class="console">{{ clientConfig }}</pre>
              </div>
              <div class="callout" style="--c:var(--info);font-size:13px">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></svg>
                <span>Agents never get an SSH shell or credential secrets. Every tool call is checked against the token's scopes, its owner's role and access rules, and written to the audit log under the token's owner.</span>
              </div>
            </div>
          </section>

          <!-- Settings: superadmin, like every other platform setting. -->
          <section v-if="canEditSettings" class="card" aria-labelledby="mcp-settings-h">
            <div class="ch"><div><h2 id="mcp-settings-h" class="ct">Settings</h2><p class="sub" style="font-size:12.5px;margin:0">Applies to every agent token.</p></div></div>
            <div v-if="settingsError && !form" class="empty-state">{{ settingsError }}</div>
            <form v-else-if="form" class="cb" style="display:flex;flex-direction:column;gap:14px;font-size:13.5px" @submit.prevent="saveSettings">
              <label class="row" style="gap:10px;align-items:flex-start">
                <input v-model="form.enabled" class="chk" type="checkbox" style="margin-top:2px" />
                <span><b>Allow MCP connections</b><span class="s2 muted" style="display:block">Turn off to reject every agent at once. Tokens still work for scripts that call the REST API.</span></span>
              </label>
              <label class="row" style="gap:10px;align-items:flex-start">
                <input v-model="form.allow_write_tools" class="chk" type="checkbox" style="margin-top:2px" :disabled="!form.enabled" />
                <span><b>Allow tools that change things</b><span class="s2 muted" style="display:block">Add hosts, run playbooks, acknowledge notifications. Off means agents can only read.</span></span>
              </label>
              <fieldset style="border:0;padding:0;margin:0;min-width:0">
                <legend class="lbl">Playbooks agents may run</legend>
                <label class="row" style="gap:8px"><input v-model="allowMode" type="radio" value="any" /> Any enabled playbook the token's owner may run</label>
                <label class="row" style="gap:8px;margin-top:4px"><input v-model="allowMode" type="radio" value="list" /> Only these</label>
                <div v-if="allowMode === 'list'" class="pick" role="group" aria-label="Playbooks agents may run">
                  <label v-for="t in templates" :key="t.id" class="row" style="gap:8px">
                    <input v-model="form.agent_template_ids" class="chk" type="checkbox" :value="t.id" />
                    <span>{{ t.name }}<span v-if="!t.enabled" class="muted"> (disabled)</span><span v-if="t.requires_approval" class="muted"> · needs approval</span></span>
                  </label>
                  <div v-if="!templates.length" class="muted">{{ templatesError || 'No playbooks yet.' }}</div>
                  <div v-for="id in unknownIds" :key="id" class="row" style="gap:8px">
                    <input v-model="form.agent_template_ids" class="chk" type="checkbox" :value="id" :aria-label="`Removed playbook ${id}`" />
                    <span class="muted mono" style="font-size:12px">{{ id }} (no longer exists)</span>
                  </div>
                </div>
                <p class="hint" style="margin:6px 0 0">Applies to every personal access token, including scripts. Runs started by an agent still go through approval when the playbook needs it.</p>
              </fieldset>
              <div>
                <label class="lbl" for="mcp-cpm">Calls per token</label>
                <select id="mcp-cpm" v-model.number="form.calls_per_minute" class="in" style="width:200px">
                  <option v-for="n in [30, 60, 120]" :key="n" :value="n">{{ n }} per minute</option>
                </select>
              </div>
              <div class="row" style="gap:10px">
                <button class="btn p" type="submit" :disabled="saving || !dirty">{{ saving ? 'Saving…' : 'Save' }}</button>
                <span v-if="settingsMsg" role="status" :style="{ color: settingsMsgBad ? 'var(--bad)' : 'var(--muted)', fontSize: '12.5px' }">{{ settingsMsg }}</span>
              </div>
            </form>
            <div v-else class="empty-state">Loading…</div>
          </section>
        </div>

        <section class="card">
          <div class="ch"><h2 class="ct">Tools <span class="cnt">{{ toolCount }}</span></h2><span class="muted" style="font-size:12.5px">A token sees only the tools its scopes allow.</span></div>
          <div class="tw"><table class="t">
            <thead><tr><th>Scope</th><th>Tools</th><th>Kind</th></tr></thead>
            <tbody>
              <tr v-for="r in TOOLS" :key="r.scope">
                <td class="mono" :class="{ muted: r.scope === 'always' }">{{ r.scope }}</td>
                <td class="mono">{{ r.tools.join(' · ') }}</td>
                <td><span class="b nd" :class="r.kind === 'Change' ? 'warn' : r.kind === 'Run' ? 'bad' : ''">{{ r.kind }}</span></td>
              </tr>
            </tbody>
          </table></div>
        </section>

        <div class="grid2" style="align-items:start">
          <section class="card">
            <div class="ch"><h2 class="ct">Agent tokens</h2><router-link v-if="auth.can('admin.security')" class="lk" to="/settings/security" style="font-size:13px">Manage tokens</router-link></div>
            <div v-if="tokenError" class="empty-state">{{ tokenError }}</div>
            <div v-else class="tw"><table class="t">
              <thead><tr><th>Token</th><th>Last call</th><th class="r"><span class="vh">Actions</span></th></tr></thead>
              <tbody>
                <tr v-for="t in tokens" :key="t.id">
                  <td><span class="nm">{{ t.name }}</span><span class="s2"><span class="mono">{{ t.token_prefix }}…</span> · {{ t.scopes.length }} scope{{ t.scopes.length === 1 ? '' : 's' }}<template v-if="t.revoked_at"> · revoked</template></span></td>
                  <td class="muted">{{ t.last_used_at ? ago(t.last_used_at) : 'Never' }}</td>
                  <td class="r"><button v-if="!t.revoked_at" class="btn sm d" type="button" @click="revoke(t)">Revoke</button></td>
                </tr>
              </tbody>
            </table>
            <div v-if="!tokens.length" class="empty-state">No agent tokens yet. Tokens you create start with <span class="mono">sr_</span>.</div></div>
            <div class="foot"><span>Shows your own tokens. Create them under Settings &rsaquo; Security &rsaquo; Personal Access Tokens.</span></div>
          </section>

          <section class="card">
            <div class="ch"><h2 class="ct">Recent tool calls</h2><router-link v-if="auth.can('admin.audit')" class="lk" :to="AUDIT_LINK" style="font-size:13px">Open audit log</router-link></div>
            <div v-if="!auth.can('admin.audit')" class="cb muted" style="font-size:13.5px">Tool calls are written to the audit log; an admin can review them there.</div>
            <div v-else-if="callsError" class="empty-state">{{ callsError }}</div>
            <template v-else>
              <div class="tw"><table class="t">
                <thead><tr><th>Time</th><th>Token</th><th>Tool</th><th>Result</th></tr></thead>
                <tbody>
                  <tr v-for="c in calls" :key="c.id">
                    <td class="mono muted" :title="new Date(c.created_at).toLocaleString()">{{ whenLabel(c.created_at) }}</td>
                    <td><span class="nm">{{ c.details?.token_name || '—' }}</span><span class="s2">{{ c.username }}</span></td>
                    <td class="mono">{{ c.details?.tool || c.resource_id }}</td>
                    <td><span class="b" :class="resultKind(c)" :title="c.details?.reason || ''">{{ resultLabel(c) }}</span></td>
                  </tr>
                </tbody>
              </table></div>
              <div v-if="!calls.length" class="empty-state">No agent tool calls recorded yet.</div>
            </template>
          </section>
        </div>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import AppShell from '@/components/layout/AppShell.vue'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
// Advertised URL is portless on purpose: agent-facing hosts run the edge on 443.
const endpoint = `${location.origin}/mcp`
const clientConfig = computed(() => JSON.stringify({
  mcpServers: { seyalrun: { type: 'http', url: endpoint, headers: { Authorization: 'Bearer sr_…' } } },
}, null, 2))
// Every MCP tool call is audited as action "mcp.tool_call".
const AUDIT_LINK = { path: '/settings/audit', query: { action: 'mcp.' } }

// The 15 tools by required scope, mirroring core/services/mcp-server/app/rpc.py
// (core/tests/test_mcp_settings_tools.py fails if the two drift apart).
const TOOLS = [
  { scope: 'always', tools: ['whoami'], kind: 'Read' },
  { scope: 'inventory:read', tools: ['list_hosts', 'get_host', 'list_zones'], kind: 'Read' },
  { scope: 'inventory:write', tools: ['create_host'], kind: 'Change' },
  { scope: 'automation:read', tools: ['list_automation_templates', 'list_job_runs'], kind: 'Read' },
  { scope: 'automation:run', tools: ['run_automation'], kind: 'Run' },
  { scope: 'audit:read', tools: ['query_audit'], kind: 'Read' },
  { scope: 'metrics:read', tools: ['get_metrics'], kind: 'Read' },
  { scope: 'sessions:read', tools: ['list_sessions'], kind: 'Read' },
  { scope: 'notifications:read', tools: ['list_notifications'], kind: 'Read' },
  { scope: 'notifications:ack', tools: ['ack_notification'], kind: 'Change' },
  { scope: 'posture:read', tools: ['get_posture_summary', 'list_findings'], kind: 'Read' },
]
const toolCount = TOOLS.reduce((n, r) => n + r.tools.length, 0)

const copied = ref('')
async function copy(text: string, key: string) {
  try { await navigator.clipboard.writeText(text) } catch { /* clipboard blocked */ }
  copied.value = key
  setTimeout(() => { if (copied.value === key) copied.value = '' }, 1500)
}

// ── Settings (superadmin) ───────────────────────────────────────────────────
interface McpSettings { enabled: boolean; allow_write_tools: boolean; calls_per_minute: number; agent_template_ids: string[] | null }
const canEditSettings = computed(() => auth.can('admin.platform'))
const form = ref<(McpSettings & { agent_template_ids: string[] }) | null>(null)
const allowMode = ref<'any' | 'list'>('any')
const saved = ref('')
const saving = ref(false)
const settingsError = ref('')
const settingsMsg = ref('')
const settingsMsgBad = ref(false)
const templates = ref<any[]>([])
const templatesError = ref('')

function snapshot(): string {
  if (!form.value) return ''
  return JSON.stringify({ ...form.value, agent_template_ids: allowMode.value === 'any' ? null : [...form.value.agent_template_ids].sort() })
}
const dirty = computed(() => !!form.value && snapshot() !== saved.value)
const unknownIds = computed(() => {
  const known = new Set(templates.value.map((t) => t.id))
  return (form.value?.agent_template_ids || []).filter((id) => !known.has(id))
})
watch(allowMode, () => { settingsMsg.value = '' })

function adopt(s: McpSettings) {
  form.value = { ...s, agent_template_ids: [...(s.agent_template_ids || [])] }
  allowMode.value = s.agent_template_ids === null ? 'any' : 'list'
  saved.value = snapshot()
}
async function loadSettings() {
  if (!canEditSettings.value) return
  try { adopt((await api.get('/settings/mcp')).data); settingsError.value = '' }
  catch (e: any) { settingsError.value = e?.response?.data?.detail || 'Could not load MCP settings.' }
  try { templates.value = (await api.get('/job-templates')).data || []; templatesError.value = '' }
  catch (e: any) { templates.value = []; templatesError.value = e?.response?.data?.detail || 'Could not load playbooks.' }
}
async function saveSettings() {
  if (!form.value) return
  saving.value = true
  settingsMsg.value = ''
  try {
    const body = { ...form.value, agent_template_ids: allowMode.value === 'any' ? null : form.value.agent_template_ids }
    adopt((await api.put('/settings/mcp', body)).data)
    settingsMsgBad.value = false
    settingsMsg.value = 'Saved. Agents see the change within about 10 seconds.'
  } catch (e: any) {
    settingsMsgBad.value = true
    const d = e?.response?.data?.detail
    settingsMsg.value = typeof d === 'string' ? d : 'Save failed.'
  } finally {
    saving.value = false
  }
}

// ── Tokens ──────────────────────────────────────────────────────────────────
const tokens = ref<any[]>([])
const tokenError = ref('')
async function loadTokens() {
  try { tokens.value = (await api.get('/api-tokens')).data || []; tokenError.value = '' }
  catch (e: any) { tokens.value = []; tokenError.value = e?.response?.data?.detail || 'Could not load tokens.' }
}
async function revoke(t: any) {
  if (!confirm(`Revoke token "${t.name}"? Agents using it stop working immediately.`)) return
  try { await api.delete(`/api-tokens/${t.id}`); await loadTokens() }
  catch (e: any) { alert(e?.response?.data?.detail || 'Revoke failed') }
}

// ── Recent tool calls (audit source=mcp) ────────────────────────────────────
const calls = ref<any[]>([])
const callsError = ref('')
async function loadCalls() {
  if (!auth.can('admin.audit')) return
  try { calls.value = (await api.get('/audit/logs', { params: { source: 'mcp', limit: 10 } })).data || []; callsError.value = '' }
  catch (e: any) { calls.value = []; callsError.value = e?.response?.data?.detail || 'Could not load tool calls.' }
}
function resultKind(c: any) {
  if (c.result === 'success') return 'ok'
  return c.details?.status === 429 ? 'warn' : 'bad'
}
function resultLabel(c: any) {
  if (c.result === 'success') return 'Success'
  const s = c.details?.status
  const r = String(c.details?.reason || '').toLowerCase()
  if (s === 429) return 'Rate limited'
  if (s === 401) return 'Denied · bad token'
  if (s === 403) {
    if (r.includes('token scope')) return 'Denied · no scope'
    if (r.includes('turned off')) return 'Denied · MCP off'
    if (r.includes('read-only')) return 'Denied · read-only'
    if (r.includes('list agents may run')) return 'Denied · not allowed'
    return 'Denied'
  }
  return s ? `Failed · ${s}` : 'Failed'
}

function ago(iso: string) {
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86400)} d ago`
}
function whenLabel(iso: string) {
  const d = new Date(iso)
  return d.toDateString() === new Date().toDateString()
    ? d.toLocaleTimeString(undefined, { hour12: false })
    : d.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}
onMounted(() => { loadTokens(); loadSettings(); loadCalls() })
</script>

<style scoped>
.pick { display: flex; flex-direction: column; gap: 6px; margin: 8px 0 0 24px; max-height: 220px; overflow: auto; }
</style>
