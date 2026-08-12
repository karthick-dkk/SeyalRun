<template>
  <AppShell>
    <div class="list-page">
      <div class="list-header">
        <div class="list-controls">
          <input v-model="search" class="prv-input" placeholder="Search playbooks…" @input="debouncedLoad" />
          <select v-model="category" class="prv-select" @change="load">
            <option value="">All categories</option>
            <option v-for="c in categories" :key="c" :value="c">{{ c }}</option>
          </select>
        </div>
        <router-link to="/playbooks/new" class="btn-primary">+ New Playbook</router-link>
      </div>

      <div v-if="loading" class="loading">Loading…</div>

      <div v-else-if="playbooks.length === 0" class="empty-state">
        No playbooks found.
        <router-link to="/playbooks/new">Create your first one.</router-link>
      </div>

      <div v-else class="playbook-grid">
        <div v-for="p in playbooks" :key="p.id" class="playbook-card">
          <div class="pb-card-header">
            <span class="pb-name">{{ p.name }}</span>
            <span class="badge badge-neutral">{{ p.category }}</span>
          </div>
          <p class="pb-desc">{{ p.description || 'No description' }}</p>
          <div class="pb-meta">
            <span class="text-muted">{{ p.tasks.length }} tasks</span>
            <span class="text-muted">by {{ p.created_by }}</span>
          </div>
          <div class="pb-tags" v-if="p.tags.length">
            <span v-for="tag in p.tags" :key="tag" class="tag">{{ tag }}</span>
          </div>
          <div class="pb-actions">
            <router-link :to="`/playbooks/${p.id}`" class="btn-sm">Edit</router-link>
            <button class="btn-sm btn-danger" @click="deletePlaybook(p.id)">Delete</button>
          </div>
        </div>
      </div>

      <div class="pagination" v-if="total > limit">
        <button :disabled="offset === 0" @click="offset -= limit; load()">‹ Prev</button>
        <span>{{ Math.floor(offset / limit) + 1 }} / {{ Math.ceil(total / limit) }}</span>
        <button :disabled="offset + limit >= total" @click="offset += limit; load()">Next ›</button>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import AppShell from '@/components/layout/AppShell.vue'
import { playbooksApi } from '@/api'
import { useUiStore } from '@/stores/ui'
import type { Playbook } from '@/types'

const ui = useUiStore()
const playbooks = ref<Playbook[]>([])
const total = ref(0)
const loading = ref(false)
const search = ref('')
const category = ref('')
const categories = ['system', 'security', 'networking', 'monitoring', 'deployment']
const limit = 20
const offset = ref(0)

let searchTimer: ReturnType<typeof setTimeout> | null = null

function debouncedLoad() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(load, 300)
}

async function load() {
  loading.value = true
  try {
    const resp = await playbooksApi.list({
      search: search.value || undefined,
      category: category.value || undefined,
      limit,
      offset: offset.value,
    })
    playbooks.value = resp.data.items
    total.value = resp.data.total
  } finally {
    loading.value = false
  }
}

async function deletePlaybook(id: string) {
  if (!confirm('Delete this playbook?')) return
  await playbooksApi.delete(id)
  ui.success('Playbook deleted')
  load()
}

onMounted(load)
</script>

<style scoped>
.list-page { display: flex; flex-direction: column; gap: var(--space-5); }
.list-header { display: flex; justify-content: space-between; align-items: center; }
.list-controls { display: flex; gap: var(--space-3); }

.prv-input, .prv-select {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  color: var(--text);
  font-size: 13px;
  padding: var(--space-2) var(--space-3);
  outline: none;
}
.prv-input:focus, .prv-select:focus { border-color: var(--accent); }
.prv-input { width: 240px; }

.btn-primary {
  background: var(--accent);
  color: #000;
  border: none;
  border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-4);
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  text-decoration: none;
}

.loading, .empty-state { text-align: center; color: var(--text-muted); padding: var(--space-10); }

.playbook-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--space-4);
}

.playbook-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  transition: border-color var(--transition);
}
.playbook-card:hover { border-color: var(--border-muted); }

.pb-card-header { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); }
.pb-name { font-weight: 600; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pb-desc { font-size: 12px; color: var(--text-muted); flex: 1; }
.pb-meta { display: flex; justify-content: space-between; font-size: 11px; }
.pb-tags { display: flex; flex-wrap: wrap; gap: var(--space-1); }
.tag { background: var(--bg-subtle); color: var(--text-muted); font-size: 11px; padding: 1px 6px; border-radius: var(--radius-full); }

.pb-actions { display: flex; gap: var(--space-2); margin-top: var(--space-2); }
.btn-sm {
  padding: var(--space-1) var(--space-3);
  font-size: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: none;
  color: var(--text-muted);
  cursor: pointer;
  text-decoration: none;
  transition: all var(--transition);
  display: inline-flex;
  align-items: center;
}
.btn-sm:hover { color: var(--text); border-color: var(--border-muted); }
.btn-danger:hover { color: var(--error); border-color: var(--error); }

.pagination { display: flex; justify-content: center; align-items: center; gap: var(--space-4); }
.pagination button { background: var(--bg-surface); border: 1px solid var(--border); color: var(--text-muted); padding: var(--space-2) var(--space-4); border-radius: var(--radius-md); cursor: pointer; }
.pagination button:disabled { opacity: 0.4; cursor: not-allowed; }
.pagination span { font-size: 12px; color: var(--text-muted); }
</style>
