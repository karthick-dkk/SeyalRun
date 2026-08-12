<template>
  <AppShell>
    <div class="alerts-page">
      <div class="tabs">
        <button
          v-for="tab in tabs"
          :key="tab"
          class="tab-btn"
          :class="{ active: activeTab === tab }"
          @click="activeTab = tab"
        >{{ tab }}</button>
      </div>

      <!-- Rules Tab -->
      <div v-if="activeTab === 'Rules'" class="tab-content">
        <div class="tab-actions">
          <button class="btn-primary" @click="showRuleForm = true">+ New Rule</button>
        </div>
        <div class="rules-list">
          <div v-for="r in rules" :key="r.id" class="rule-card">
            <div class="rule-header">
              <span class="rule-name">{{ r.name }}</span>
              <div class="rule-badges">
                <span :class="`badge ${r.enabled ? 'badge-success' : 'badge-neutral'}`">{{ r.enabled ? 'enabled' : 'disabled' }}</span>
                <span class="badge badge-info">{{ r.event_type }}</span>
              </div>
            </div>
            <p class="rule-desc text-muted">{{ r.description || 'No description' }}</p>
            <div class="rule-actions">
              <button class="btn-sm" @click="testRule(r.id)">Test</button>
              <button class="btn-sm btn-toggle" @click="toggleRule(r)">{{ r.enabled ? 'Disable' : 'Enable' }}</button>
              <button class="btn-sm btn-danger" @click="deleteRule(r.id)">Delete</button>
            </div>
          </div>
          <div v-if="rules.length === 0" class="empty-state">No alert rules configured.</div>
        </div>
      </div>

      <!-- History Tab -->
      <div v-if="activeTab === 'History'" class="tab-content">
        <div class="history-list">
          <div v-for="h in history" :key="h.id" class="history-row">
            <span :class="`badge badge-${h.delivery_status === 'delivered' ? 'success' : 'error'}`">{{ h.delivery_status }}</span>
            <span class="history-rule">{{ h.rule_name }}</span>
            <span class="badge badge-neutral">{{ h.event_type }}</span>
            <span class="text-muted">{{ formatTime(h.delivered_at) }}</span>
          </div>
          <div v-if="history.length === 0" class="empty-state">No alert history.</div>
        </div>
      </div>

      <!-- Channels Tab -->
      <div v-if="activeTab === 'Channels'" class="tab-content">
        <div class="tab-actions">
          <button class="btn-primary" @click="showChannelForm = true">+ Add Channel</button>
        </div>
        <div class="channels-list">
          <div v-for="c in channels" :key="c.id" class="channel-card">
            <div class="ch-header">
              <span class="ch-name">{{ c.name }}</span>
              <span class="badge badge-neutral">{{ c.channel_type }}</span>
              <span :class="`badge ${c.is_active ? 'badge-success' : 'badge-neutral'}`">{{ c.is_active ? 'active' : 'inactive' }}</span>
            </div>
            <div class="ch-actions">
              <button class="btn-sm" @click="testChannel(c.id)">Test</button>
              <button class="btn-sm btn-danger" @click="deleteChannel(c.id)">Delete</button>
            </div>
          </div>
          <div v-if="channels.length === 0" class="empty-state">No notification channels.</div>
        </div>
      </div>
    </div>

    <!-- Simple rule creation modal -->
    <div v-if="showRuleForm" class="modal-overlay" @click.self="showRuleForm = false">
      <div class="modal">
        <div class="modal-header">
          <h3>New Alert Rule</h3>
          <button @click="showRuleForm = false">✕</button>
        </div>
        <div class="rule-form">
          <div class="field"><label>Name</label><input v-model="newRule.name" class="prv-input" /></div>
          <div class="field">
            <label>Event Type</label>
            <select v-model="newRule.event_type" class="prv-select">
              <option v-for="et in eventTypes" :key="et" :value="et">{{ et }}</option>
            </select>
          </div>
          <div class="field"><label>Webhook URL (optional)</label><input v-model="webhookUrl" class="prv-input" placeholder="https://…" /></div>
          <button class="btn-primary" @click="createRule">Create Rule</button>
        </div>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import AppShell from '@/components/layout/AppShell.vue'
import { alertsApi } from '@/api'
import { useEscapeKey } from '@/composables/useEscapeKey'
import { useUiStore } from '@/stores/ui'
import type { AlertRule, AlertHistory, NotificationChannel } from '@/types'

const ui = useUiStore()
const tabs = ['Rules', 'History', 'Channels']
const activeTab = ref('Rules')
const rules = ref<AlertRule[]>([])
const history = ref<AlertHistory[]>([])
const channels = ref<NotificationChannel[]>([])
const showRuleForm = ref(false)
const showChannelForm = ref(false)

useEscapeKey(() => {
  if (showRuleForm.value) { showRuleForm.value = false; return }
  if (showChannelForm.value) { showChannelForm.value = false }
})
const eventTypes = ['job_failed', 'job_slow', 'connectivity_lost', 'ldap_sync_failed', 'zabbix_webhook']
const webhookUrl = ref('')

const newRule = reactive({ name: '', event_type: 'job_failed', enabled: true })

function formatTime(iso: string) { return new Date(iso).toLocaleString() }

async function loadRules()    { rules.value    = (await alertsApi.rules.list()).data.items }
async function loadHistory()  { history.value  = (await alertsApi.history()).data.items }
async function loadChannels() { channels.value = (await alertsApi.channels.list()).data.items }

async function testRule(id: string) {
  await alertsApi.rules.test(id)
  ui.success('Test alert fired')
}

async function toggleRule(r: AlertRule) {
  await alertsApi.rules.patch(r.id, { enabled: !r.enabled })
  ui.success(r.enabled ? 'Rule disabled' : 'Rule enabled')
  loadRules()
}

async function deleteRule(id: string) {
  if (!confirm('Delete rule?')) return
  await alertsApi.rules.delete(id)
  ui.success('Rule deleted')
  loadRules()
}

async function testChannel(id: string) {
  const resp = await alertsApi.channels.test(id)
  if ((resp.data as {result?: {success?: boolean}}).result?.success) ui.success('Test delivery succeeded')
  else ui.error('Test delivery failed')
}

async function deleteChannel(id: string) {
  if (!confirm('Delete channel?')) return
  await alertsApi.channels.delete(id)
  ui.success('Channel deleted')
  loadChannels()
}

async function createRule() {
  const channels = webhookUrl.value
    ? [{ type: 'webhook' as const, url: webhookUrl.value }]
    : []
  await alertsApi.rules.create({
    name: newRule.name,
    description: '',
    enabled: true,
    event_type: newRule.event_type,
    conditions: {},
    channels,
  })
  ui.success('Rule created')
  showRuleForm.value = false
  loadRules()
}

onMounted(() => {
  loadRules()
  loadHistory()
  loadChannels()
})
</script>

<style scoped>
.alerts-page { display: flex; flex-direction: column; gap: var(--space-5); }
.tabs { display: flex; gap: var(--space-1); border-bottom: 1px solid var(--border); }
.tab-btn { padding: var(--space-2) var(--space-4); background: none; border: none; border-bottom: 2px solid transparent; color: var(--text-muted); font-size: 13px; cursor: pointer; margin-bottom: -1px; transition: all var(--transition); }
.tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }
.tab-content { display: flex; flex-direction: column; gap: var(--space-4); }
.tab-actions { display: flex; justify-content: flex-end; }
.btn-primary { background: var(--accent); color: #000; border: none; border-radius: var(--radius-md); padding: var(--space-2) var(--space-4); font-size: 13px; font-weight: 700; cursor: pointer; }

.rules-list, .channels-list { display: flex; flex-direction: column; gap: var(--space-3); }
.rule-card, .channel-card { background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: var(--space-4); display: flex; flex-direction: column; gap: var(--space-2); }
.rule-header, .ch-header { display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; }
.rule-name, .ch-name { font-weight: 600; font-size: 14px; flex: 1; }
.rule-badges, .ch-header { display: flex; gap: var(--space-2); }
.rule-desc { font-size: 12px; }
.rule-actions, .ch-actions { display: flex; gap: var(--space-2); }
.btn-sm { padding: var(--space-1) var(--space-3); font-size: 12px; border: 1px solid var(--border); border-radius: var(--radius-md); background: none; color: var(--text-muted); cursor: pointer; transition: all var(--transition); }
.btn-sm:hover { color: var(--text); border-color: var(--border-muted); }
.btn-danger:hover { color: var(--error); border-color: var(--error); }

.history-list { display: flex; flex-direction: column; gap: var(--space-2); }
.history-row { display: flex; align-items: center; gap: var(--space-3); padding: var(--space-2) var(--space-4); background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-md); font-size: 13px; }
.history-rule { flex: 1; }

.empty-state { text-align: center; color: var(--text-muted); padding: var(--space-8); font-size: 13px; }

/* Modal */
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.7); display: flex; align-items: center; justify-content: center; z-index: 500; }
.modal { background: var(--bg-surface); border: 1px solid var(--border-muted); border-radius: var(--radius-xl); width: 480px; overflow: hidden; box-shadow: var(--shadow-lg); }
.modal-header { display: flex; align-items: center; justify-content: space-between; padding: var(--space-4) var(--space-5); border-bottom: 1px solid var(--border); }
.modal-header button { background: none; border: none; color: var(--text-muted); font-size: 16px; cursor: pointer; }
.rule-form { padding: var(--space-5); display: flex; flex-direction: column; gap: var(--space-4); }
.field { display: flex; flex-direction: column; gap: var(--space-1); }
.field label { font-size: 11px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; }
.prv-input, .prv-select { background: var(--bg-base); border: 1px solid var(--border); border-radius: var(--radius-md); color: var(--text); font-size: 13px; padding: var(--space-2) var(--space-3); outline: none; width: 100%; }
.prv-input:focus, .prv-select:focus { border-color: var(--accent); }
</style>
