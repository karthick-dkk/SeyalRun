<template>
  <div class="stack" style="gap:10px">
    <h3 class="ct" style="margin:0">Package operations <span class="cnt">{{ ops.length }}</span></h3>
    <div v-if="auth.isAdmin" class="card" style="padding:12px">
      <div class="stack" style="gap:10px">
        <div class="row" style="flex-wrap:wrap;align-items:flex-end">
          <div><label for="po-op" class="lbl">Operation</label>
            <select id="po-op" v-model="op" class="in" style="width:auto"><option v-for="o in OPS" :key="o.v" :value="o.v">{{ o.t }}</option></select></div>
          <div v-if="needsPkgs" style="flex:1 1 240px"><label for="po-pk" class="lbl">Package names, one per line</label>
            <textarea id="po-pk" v-model="pkgText" class="in mono" rows="3" style="height:auto;padding:8px 10px;width:100%" placeholder="nginx&#10;openssl"></textarea></div>
          <button class="btn p" type="button" :disabled="busy || (needsPkgs && !names.length)" @click="run">{{ busy ? 'Queuing…' : 'Run operation' }}</button>
        </div>
        <div class="hint">{{ OPS.find((o) => o.v === op)?.hint }}</div>
        <div v-if="err" class="callout" style="--c:var(--bad)" role="alert">{{ err }}</div>
        <div v-if="msg" class="callout" style="--c:var(--ok)" role="status">{{ msg }}</div>
      </div>
    </div>
    <div class="card"><PackageOpsTable :ops="ops" :show-host="false" /></div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useConfirm } from '@/composables/useConfirm'
import { detailOf } from '@/composables/patchingUtil'
import PackageOpsTable from './PackageOpsTable.vue'

const props = defineProps<{ hostId: string; hostname: string }>()
const auth = useAuthStore()
const { confirm } = useConfirm()
const OPS = [
  { v: 'plan', t: 'Plan', hint: 'Show what would change. Nothing is installed.' },
  { v: 'refresh', t: 'Refresh package lists', hint: 'Update the package index. Installed packages do not change.' },
  { v: 'install', t: 'Install', hint: 'Installs the named packages.' },
  { v: 'upgrade', t: 'Upgrade', hint: 'Upgrades the named packages.' },
  { v: 'remove', t: 'Remove', hint: 'Removes the named packages.' },
  { v: 'purge', t: 'Purge', hint: 'Removes the named packages and their configuration.' },
  { v: 'hold', t: 'Hold', hint: 'Stops the named packages from being upgraded.' },
  { v: 'unhold', t: 'Unhold', hint: 'Allows the named packages to be upgraded again.' },
  { v: 'pin', t: 'Pin', hint: 'Pins the named packages.' },
  { v: 'repo_apply', t: 'Apply mirrors', hint: 'Writes the configured mirrors for this host.' },
]
const op = ref('plan')
const pkgText = ref('')
const busy = ref(false)
const err = ref('')
const msg = ref('')
const ops = ref<any[]>([])
const needsPkgs = computed(() => !['refresh', 'repo_apply'].includes(op.value))
const names = computed(() => [...new Set(pkgText.value.split('\n').map((s) => s.trim()).filter(Boolean))])
const readOnly = computed(() => op.value === 'plan' || op.value === 'refresh')

async function load() {
  try { ops.value = (await api.get('/posture/package-ops', { params: { host_id: props.hostId, limit: 20 } })).data || [] } catch { ops.value = [] }
}
async function run() {
  err.value = ''; msg.value = ''
  const pk = needsPkgs.value ? names.value : []
  const lbl = OPS.find((o) => o.v === op.value)!.t
  const text = readOnly.value
    ? `Run "${lbl}" on ${props.hostname}?\n\nThis does not change packages on the host.`
    : `Run "${lbl}" on ${props.hostname}?\n\nThis changes the host.${pk.length ? `\nPackages (${pk.length}): ${pk.slice(0, 10).join(', ')}${pk.length > 10 ? ', …' : ''}` : ''}`
  if (!(await confirm(text, { title: 'Package operation', confirmLabel: 'Run', danger: !readOnly.value }))) return
  busy.value = true
  try {
    const r = (await api.post(`/posture/hosts/${props.hostId}/packages`, { op: op.value, packages: pk.map((name) => ({ name })) })).data
    msg.value = r?.status === 'awaiting_approval' ? 'Queued. The job run is waiting for approval.' : 'Queued.'
    await load()
  } catch (e: any) { err.value = detailOf(e, 'Could not start the operation.') } finally { busy.value = false }
}
onMounted(load)
watch(() => props.hostId, load)
</script>
