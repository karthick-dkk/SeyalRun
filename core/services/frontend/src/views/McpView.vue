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
            <p class="sub">Let AI agents such as Claude work with SeyalRun over the Model Context Protocol, inside the same permissions and audit trail as everyone else.</p>
          </div>
        </div>
        <div class="acts">
          <router-link v-if="auth.can('admin.audit')" class="btn" to="/settings/audit">Tool-call audit</router-link>
          <router-link v-if="auth.can('admin.security')" class="btn p" to="/settings/security">New token</router-link>
        </div>
      </div>

      <div class="stack">
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
              <span>Agents never get credential secrets. Every tool call is checked against the token's scopes, its owner's role and access rules, and written to the audit log under the token's owner.</span>
            </div>
          </div>
        </section>

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
            <div class="ch"><h2 class="ct">Recent tool calls</h2><router-link v-if="auth.can('admin.audit')" class="lk" to="/settings/audit" style="font-size:13px">Open audit log</router-link></div>
            <div class="cb muted" style="font-size:13.5px">
              Tool calls are recorded in the audit log under the token owner's name, as the same actions a person would produce (for example <span class="mono">job.start</span> or <span class="mono">host.create</span>). The audit API cannot yet filter to MCP calls only, so open the audit log and filter by the token owner to review an agent's activity.
            </div>
          </section>
        </div>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import AppShell from '@/components/layout/AppShell.vue'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
// Advertised URL is portless on purpose: agent-facing hosts run the edge on 443.
const endpoint = `${location.origin}/mcp`
const clientConfig = computed(() => JSON.stringify({
  mcpServers: { seyalrun: { type: 'http', url: endpoint, headers: { Authorization: 'Bearer sr_…' } } },
}, null, 2))

// The 13 tools by required scope, mirroring core/services/mcp-server/app/rpc.py.
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
]
const toolCount = TOOLS.reduce((n, r) => n + r.tools.length, 0)

const copied = ref('')
async function copy(text: string, key: string) {
  try { await navigator.clipboard.writeText(text) } catch { /* clipboard blocked */ }
  copied.value = key
  setTimeout(() => { if (copied.value === key) copied.value = '' }, 1500)
}

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
function ago(iso: string) {
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86400)} d ago`
}
onMounted(loadTokens)
</script>
