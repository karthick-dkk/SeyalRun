<template>
  <AppShell>
    <div v-if="template" class="detail-page">
      <div class="detail-header">
        <router-link to="/templates" class="back-link">← Templates</router-link>
        <div class="detail-title-row">
          <h1 class="detail-title">{{ template.name }}</h1>
          <span :class="`badge badge-${riskBadge(template.risk_level)}`">{{ template.risk_level }} risk</span>
          <span class="badge badge-neutral">{{ template.category }}</span>
        </div>
        <p class="detail-desc">{{ template.description }}</p>
        <div class="detail-meta">
          <span>{{ template.tasks.length }} tasks</span>
          <span>~{{ Math.round(template.estimated_duration_seconds / 60) }} min</span>
        </div>
      </div>

      <div class="detail-body">
        <div class="tasks-section">
          <h2>Tasks</h2>
          <div class="task-preview" v-for="(t, i) in template.tasks" :key="t.task_id">
            <span class="task-num">{{ i + 1 }}</span>
            <div>
              <div class="task-name">{{ t.name }}</div>
              <div class="task-module text-muted">{{ t.module }}</div>
            </div>
          </div>
        </div>

        <div class="clone-section">
          <h2>Run on Hosts</h2>
          <button class="btn-run-big" @click="showRun = true">
            ▶ Run This Template
          </button>

          <div class="divider">or clone to edit first</div>

          <div class="clone-form">
            <input v-model="cloneName" class="prv-input" :placeholder="`${template.name} (copy)`" />
            <button class="btn-primary" @click="clone" :disabled="cloning">
              {{ cloning ? 'Cloning…' : 'Clone to Playbook' }}
            </button>
          </div>
        </div>
      </div>
    </div>
    <div v-else class="loading">Loading…</div>

    <RunTemplateModal
      v-if="showRun && template"
      :template="template"
      @close="showRun = false"
    />
  </AppShell>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'
import RunTemplateModal from '@/components/template-library/RunTemplateModal.vue'
import { useEscapeKey } from '@/composables/useEscapeKey'
import { templatesApi } from '@/api'
import { useUiStore } from '@/stores/ui'
import type { Template } from '@/types'

const route = useRoute()
const router = useRouter()
const ui = useUiStore()
const template = ref<Template | null>(null)
const cloneName = ref('')
const cloning = ref(false)
const showRun = ref(false)
useEscapeKey(() => { if (showRun.value) showRun.value = false })

function riskBadge(r: string) {
  return r === 'high' ? 'error' : r === 'medium' ? 'warning' : 'success'
}

onMounted(async () => {
  const resp = await templatesApi.get(route.params.slug as string)
  template.value = resp.data
})

async function clone() {
  if (!template.value) return
  cloning.value = true
  try {
    const resp = await templatesApi.clone(template.value.slug, { name: cloneName.value || undefined })
    ui.success('Template cloned to playbook')
    router.push(`/playbooks/${resp.data.id}`)
  } catch {
    ui.error('Clone failed')
  } finally {
    cloning.value = false
  }
}
</script>

<style scoped>
.detail-page { display: flex; flex-direction: column; gap: var(--space-6); }
.back-link { font-size: 13px; color: var(--text-muted); }
.detail-title-row { display: flex; align-items: center; gap: var(--space-3); }
.detail-title { font-size: 22px; font-weight: 700; }
.detail-desc { color: var(--text-muted); max-width: 600px; }
.detail-meta { display: flex; gap: var(--space-4); font-size: 13px; color: var(--text-muted); font-family: var(--font-mono); }

.detail-body { display: grid; grid-template-columns: 1fr 360px; gap: var(--space-6); }

.tasks-section, .clone-section {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: var(--space-5);
}
.tasks-section h2, .clone-section h2 { font-size: 14px; font-weight: 600; margin-bottom: var(--space-4); }

.task-preview {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--border);
}
.task-preview:last-child { border-bottom: none; }
.task-num {
  width: 24px;
  height: 24px;
  border-radius: var(--radius-full);
  background: var(--bg-subtle);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-family: var(--font-mono);
  flex-shrink: 0;
}
.task-name { font-size: 13px; font-weight: 500; }
.task-module { font-size: 11px; font-family: var(--font-mono); }

.clone-form { display: flex; flex-direction: column; gap: var(--space-3); }
.prv-input {
  background: var(--bg-base);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  color: var(--text);
  font-size: 13px;
  padding: var(--space-3);
  outline: none;
}
.prv-input:focus { border-color: var(--accent); }
.btn-run-big {
  width: 100%;
  background: var(--success);
  color: #000;
  border: none;
  border-radius: var(--radius-md);
  padding: var(--space-3);
  font-size: 15px;
  font-weight: 700;
  cursor: pointer;
  transition: background var(--transition);
}
.btn-run-big:hover { background: #4cae5e; }

.divider {
  text-align: center;
  font-size: 11px;
  color: var(--text-subtle);
  position: relative;
  margin: var(--space-1) 0;
}
.divider::before, .divider::after {
  content: '';
  position: absolute;
  top: 50%;
  width: 35%;
  height: 1px;
  background: var(--border);
}
.divider::before { left: 0; }
.divider::after { right: 0; }

.btn-primary {
  background: var(--accent);
  color: #000;
  border: none;
  border-radius: var(--radius-md);
  padding: var(--space-3);
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.loading { text-align: center; color: var(--text-muted); padding: var(--space-10); }
</style>
