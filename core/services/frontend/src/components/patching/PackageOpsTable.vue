<template>
  <div>
    <div class="tw"><table class="t">
      <thead><tr><th></th><th>When</th><th v-if="showHost">Host</th><th>Operation</th><th class="r">Packages</th><th>Status</th><th>Requested by</th><th>Run</th></tr></thead>
      <tbody>
        <template v-for="o in ops" :key="o.id">
          <tr>
            <td><button class="btn sm ib g" type="button" :aria-label="`${open[o.id] ? 'Hide' : 'Show'} result`" :aria-expanded="!!open[o.id]" @click="open[o.id] = !open[o.id]">{{ open[o.id] ? '▾' : '▸' }}</button></td>
            <td class="muted">{{ o.created_at ? ago(o.created_at) : '—' }}</td>
            <td v-if="showHost">{{ hostNames[o.host_id] || o.host_id }}</td>
            <td><span class="tag">{{ o.op }}</span></td>
            <td class="r num">{{ (o.packages || []).length }}</td>
            <td><span class="b" :class="opKind(o.status)">{{ label(o.status) }}</span></td>
            <td>{{ o.requested_by_name || '—' }}</td>
            <td><router-link v-if="o.job_run_id" class="lk" :to="`/jobs/${o.job_run_id}`">View run</router-link><span v-else class="muted">—</span></td>
          </tr>
          <tr v-if="open[o.id]">
            <td></td>
            <td :colspan="showHost ? 7 : 6" style="white-space:normal">
              <div class="stack" style="gap:10px">
                <div v-if="o.error" class="callout" style="--c:var(--bad)" role="alert">{{ o.error }}</div>
                <div v-if="(o.packages || []).length"><b>Requested: </b><span v-for="p in o.packages" :key="p.name + (p.arch || '')" class="tag mono" style="margin:0 4px 4px 0">{{ p.name }}{{ p.arch ? '.' + p.arch : '' }}{{ p.version ? ' ' + p.version : '' }}</span></div>
                <div v-if="(o.result?.changes || []).length">
                  <b>Changes</b>
                  <ul style="margin:6px 0 0;padding-left:18px"><li v-for="c in o.result.changes" :key="c.name" class="mono">{{ c.name }}: {{ c.old || '(none)' }} → {{ c.new || '(none)' }}</li></ul>
                </div>
                <div v-else-if="o.status === 'ok'" class="muted">No package changes recorded.</div>
                <div v-if="(o.result?.held || []).length"><b>Held: </b><span v-for="x in o.result.held" :key="String(x)" class="tag mono" style="margin-right:4px">{{ typeof x === 'string' ? x : (x.name || JSON.stringify(x)) }}</span></div>
                <div v-if="o.result?.plan"><b>Plan</b><pre class="mono" style="margin:6px 0 0;white-space:pre-wrap;max-height:200px;overflow:auto">{{ typeof o.result.plan === 'string' ? o.result.plan : JSON.stringify(o.result.plan, null, 2) }}</pre></div>
                <div v-if="o.result?.exit_code != null" class="muted">Exit code {{ o.result.exit_code }}. Finished {{ when(o.finished_at) }}.</div>
              </div>
            </td>
          </tr>
        </template>
      </tbody>
    </table></div>
    <div v-if="!ops.length" class="empty-state"><b>No package operations</b>Operations run from a host's detail panel appear here.</div>
  </div>
</template>

<script setup lang="ts">
import { reactive } from 'vue'
import { ago, label, opKind, when } from '@/composables/patchingUtil'
withDefaults(defineProps<{ ops: any[]; showHost?: boolean; hostNames?: Record<string, string> }>(), { showHost: true, hostNames: () => ({}) })
const open = reactive<Record<string, boolean>>({})
</script>
