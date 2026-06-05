<template>
  <AppShell>
    <div class="builder">
      <!-- Left: Task list + meta -->
      <div class="builder-left">
        <!-- Playbook meta -->
        <div class="meta-card">
          <input v-model="pb.name" class="pb-name-input" placeholder="Playbook name…" />
          <input v-model="pb.description" class="pb-desc-input" placeholder="Description (optional)" />
          <div class="meta-row">
            <select v-model="pb.category" class="prv-select">
              <option v-for="c in categories" :key="c" :value="c">{{ c }}</option>
            </select>
            <input v-model="tagsInput" class="prv-input" placeholder="Tags (comma-separated)" />
          </div>
        </div>

        <!-- Task list -->
        <div class="task-list-header">
          <span>Tasks ({{ pb.tasks.length }})</span>
          <button class="btn-add" @click="showModulePicker = true">+ Add Task</button>
        </div>

        <div v-if="pb.tasks.length === 0" class="empty-tasks">
          <p>No tasks yet.</p>
          <button class="btn-add" @click="showModulePicker = true">+ Add your first task</button>
        </div>

        <div v-else class="task-list">
          <div
            v-for="(task, idx) in pb.tasks"
            :key="task.task_id"
            class="task-card"
            :class="{ 'task-card--active': selectedIdx === idx }"
            @click="selectedIdx = idx"
          >
            <div class="task-index">{{ idx + 1 }}</div>
            <div class="task-info">
              <div class="task-name">{{ task.name || '(unnamed)' }}</div>
              <div class="task-module">{{ task.module }}</div>
            </div>
            <div class="task-actions">
              <button @click.stop="moveTask(idx, -1)" :disabled="idx === 0" title="Move up">↑</button>
              <button @click.stop="moveTask(idx, 1)" :disabled="idx === pb.tasks.length - 1" title="Move down">↓</button>
              <button @click.stop="removeTask(idx)" title="Remove" class="btn-remove">✕</button>
            </div>
          </div>
        </div>

        <div class="builder-actions">
          <button class="btn-secondary" @click="router.back()">Cancel</button>
          <button class="btn-primary" @click="savePlaybook" :disabled="saving">
            {{ saving ? 'Saving…' : (playbookId ? 'Update' : 'Create') }}
          </button>
        </div>
      </div>

      <!-- Right: Task editor + YAML preview -->
      <div class="builder-right">
        <div v-if="selectedTask" class="task-editor">
          <h3 class="editor-title">Edit Task</h3>
          <div class="editor-field">
            <label>Task Name</label>
            <input v-model="selectedTask.name" class="prv-input" placeholder="e.g. Install nginx" />
          </div>
          <div class="editor-field">
            <label>Become (sudo)</label>
            <input type="checkbox" v-model="selectedTask.become" />
          </div>
          <div class="editor-field">
            <label>When (condition)</label>
            <input v-model="selectedTask.when" class="prv-input" placeholder="ansible_os_family == 'Debian'" />
          </div>
          <div class="editor-field">
            <label>Register var</label>
            <input v-model="selectedTask.register" class="prv-input" placeholder="result" />
          </div>

          <h4 class="params-title">Parameters — {{ selectedTask.module }}</h4>
          <ModuleParamForm
            v-if="selectedModuleParams"
            :params="selectedModuleParams"
            v-model="selectedTask.params"
          />
        </div>
        <div v-else class="no-task-selected">Select a task to edit</div>

        <div class="yaml-section">
          <YamlPreview :content="yamlContent" />
        </div>
      </div>
    </div>

    <!-- Module picker modal -->
    <div v-if="showModulePicker" class="modal-overlay" @click.self="showModulePicker = false">
      <div class="modal">
        <div class="modal-header">
          <h3>Pick a Module</h3>
          <button @click="showModulePicker = false">✕</button>
        </div>
        <ModulePicker @pick="onModuleSelected" />
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { useEscapeKey } from '@/composables/useEscapeKey'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'
import ModulePicker from '@/components/playbook-builder/ModulePicker.vue'
import ModuleParamForm from '@/components/playbook-builder/ModuleParamForm.vue'
import YamlPreview from '@/components/playbook-builder/YamlPreview.vue'
import { playbooksApi, modulesApi } from '@/api'
import { useUiStore } from '@/stores/ui'
import type { ModuleInfo, TaskDefinition } from '@/types'

const route = useRoute()
const router = useRouter()
const ui = useUiStore()

const playbookId = computed(() => route.params.id as string | undefined)
const saving = ref(false)
const showModulePicker = ref(false)
useEscapeKey(() => { if (showModulePicker.value) showModulePicker.value = false })
const selectedIdx = ref<number>(-1)
const moduleCache = ref<Record<string, ModuleInfo>>({})

const categories = ['system', 'security', 'networking', 'monitoring', 'deployment', 'other']

const pb = reactive({
  name: '',
  description: '',
  category: 'system',
  tags: [] as string[],
  tasks: [] as TaskDefinition[],
  variables: [] as { name: string; default_value: string; description: string; required: boolean }[],
})

const tagsInput = ref('')
watch(tagsInput, (v) => { pb.tags = v.split(',').map((t) => t.trim()).filter(Boolean) })

const selectedTask = computed<TaskDefinition | undefined>(() =>
  selectedIdx.value >= 0 ? pb.tasks[selectedIdx.value] : undefined
)

const selectedModuleParams = computed(() => {
  if (!selectedTask.value) return undefined
  return moduleCache.value[selectedTask.value.module]?.params
})

const yamlContent = ref('')
let yamlTimer: ReturnType<typeof setTimeout> | null = null

watch(
  () => JSON.stringify(pb),
  () => {
    if (yamlTimer) clearTimeout(yamlTimer)
    yamlTimer = setTimeout(refreshYaml, 300)
  },
  { deep: true }
)

async function refreshYaml() {
  if (!playbookId.value || pb.tasks.length === 0) return
  try {
    const resp = await playbooksApi.yaml(playbookId.value)
    yamlContent.value = resp.data.yaml
  } catch { /* ignore */ }
}

onMounted(async () => {
  if (playbookId.value) {
    const resp = await playbooksApi.get(playbookId.value)
    const p = resp.data
    pb.name = p.name
    pb.description = p.description ?? ''
    pb.category = p.category
    pb.tags = p.tags
    pb.tasks = p.tasks
    pb.variables = p.variables
    tagsInput.value = p.tags.join(', ')
    await refreshYaml()
  }
})

function onModuleSelected(mod: ModuleInfo) {
  moduleCache.value[mod.name] = mod
  const task: TaskDefinition = {
    task_id: crypto.randomUUID(),
    name: `${mod.short_name} task`,
    module: mod.name,
    params: {},
    become: false,
  }
  pb.tasks.push(task)
  selectedIdx.value = pb.tasks.length - 1
  showModulePicker.value = false
}

async function ensureModuleParams(modName: string) {
  if (moduleCache.value[modName]) return
  const resp = await modulesApi.get(modName)
  moduleCache.value[modName] = resp.data
}

watch(selectedTask, async (t) => {
  if (t) await ensureModuleParams(t.module)
})

function moveTask(idx: number, dir: -1 | 1) {
  const newIdx = idx + dir
  if (newIdx < 0 || newIdx >= pb.tasks.length) return
  const tasks = [...pb.tasks]
  ;[tasks[idx], tasks[newIdx]] = [tasks[newIdx], tasks[idx]]
  pb.tasks = tasks
  selectedIdx.value = newIdx
}

function removeTask(idx: number) {
  pb.tasks.splice(idx, 1)
  if (selectedIdx.value >= pb.tasks.length) selectedIdx.value = pb.tasks.length - 1
}

async function savePlaybook() {
  if (!pb.name.trim()) { ui.error('Playbook name is required'); return }
  saving.value = true
  try {
    const payload = {
      name: pb.name,
      description: pb.description,
      category: pb.category,
      tags: pb.tags,
      tasks: pb.tasks,
      variables: pb.variables,
    }
    if (playbookId.value) {
      await playbooksApi.update(playbookId.value, payload)
      ui.success('Playbook updated')
    } else {
      const resp = await playbooksApi.create(payload)
      ui.success('Playbook created')
      router.replace(`/playbooks/${resp.data.id}`)
    }
    await refreshYaml()
  } catch (e: unknown) {
    ui.error((e as Error)?.message ?? 'Save failed')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.builder {
  display: grid;
  grid-template-columns: 380px 1fr;
  gap: var(--space-4);
  height: calc(100vh - var(--topbar-height) - 2 * var(--space-6));
}

.builder-left {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  overflow-y: auto;
}

.meta-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.pb-name-input {
  background: none;
  border: none;
  border-bottom: 1px solid var(--border);
  color: var(--text);
  font-size: 16px;
  font-weight: 600;
  padding: var(--space-1) 0;
  outline: none;
  width: 100%;
}
.pb-name-input::placeholder { color: var(--text-subtle); }

.pb-desc-input {
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 13px;
  padding: 2px 0;
  outline: none;
  width: 100%;
}

.meta-row { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-2); }

.prv-select, .prv-input {
  background: var(--bg-base);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  color: var(--text);
  font-size: 12px;
  font-family: var(--font-mono);
  padding: var(--space-2) var(--space-3);
  outline: none;
}
.prv-select:focus, .prv-input:focus { border-color: var(--accent); }

.task-list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
}

.btn-add {
  background: var(--accent-dim);
  color: var(--accent);
  border: 1px solid var(--accent);
  border-radius: var(--radius-md);
  padding: var(--space-1) var(--space-3);
  font-size: 12px;
  cursor: pointer;
  transition: all var(--transition);
}
.btn-add:hover { background: var(--accent); color: #000; }

.empty-tasks {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-4);
  color: var(--text-muted);
  font-size: 13px;
}

.task-list { display: flex; flex-direction: column; gap: var(--space-2); flex: 1; overflow-y: auto; }

.task-card {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--transition);
}
.task-card:hover { border-color: var(--border-muted); }
.task-card--active { border-color: var(--accent); background: var(--accent-dim); }

.task-index {
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

.task-info { flex: 1; min-width: 0; }
.task-name   { font-size: 13px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.task-module { font-size: 10px; color: var(--text-muted); font-family: var(--font-mono); }

.task-actions { display: flex; gap: 2px; flex-shrink: 0; }
.task-actions button {
  background: none;
  border: none;
  color: var(--text-subtle);
  cursor: pointer;
  font-size: 11px;
  padding: 2px 4px;
  border-radius: var(--radius-sm);
  transition: all var(--transition);
}
.task-actions button:hover { color: var(--text); background: var(--bg-overlay); }
.task-actions button:disabled { opacity: 0.3; cursor: not-allowed; }
.btn-remove:hover { color: var(--error) !important; }

.builder-actions {
  display: flex;
  gap: var(--space-2);
  justify-content: flex-end;
  padding-top: var(--space-3);
}

.btn-primary {
  background: var(--accent);
  color: #000;
  border: none;
  border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-5);
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  transition: background var(--transition);
}
.btn-primary:hover:not(:disabled) { background: var(--accent-hover); }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }

.btn-secondary {
  background: none;
  color: var(--text-muted);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-4);
  font-size: 13px;
  cursor: pointer;
  transition: all var(--transition);
}
.btn-secondary:hover { color: var(--text); border-color: var(--border-muted); }

.builder-right {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  overflow: hidden;
}

.task-editor {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: var(--space-5);
  overflow-y: auto;
  flex-shrink: 0;
  max-height: 50%;
}

.editor-title { font-size: 14px; font-weight: 600; margin-bottom: var(--space-4); }
.editor-field { display: flex; flex-direction: column; gap: var(--space-1); margin-bottom: var(--space-3); }
.editor-field label { font-size: 11px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; }

.params-title { font-size: 12px; font-weight: 600; color: var(--text-muted); margin: var(--space-4) 0 var(--space-3); border-top: 1px solid var(--border); padding-top: var(--space-4); }

.no-task-selected {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-subtle);
  font-size: 13px;
  background: var(--bg-surface);
  border: 1px dashed var(--border);
  border-radius: var(--radius-lg);
  min-height: 200px;
}

.yaml-section { flex: 1; overflow: hidden; }

/* Modal */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 500;
}

.modal {
  background: var(--bg-surface);
  border: 1px solid var(--border-muted);
  border-radius: var(--radius-xl);
  width: 90vw;
  max-width: 800px;
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: var(--shadow-lg);
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--border);
}
.modal-header h3 { font-size: 15px; }
.modal-header button {
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 16px;
  cursor: pointer;
}

:deep(.module-picker) { padding: var(--space-4); }
</style>
