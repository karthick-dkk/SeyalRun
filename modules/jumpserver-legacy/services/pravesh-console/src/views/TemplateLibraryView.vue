<template>
  <AppShell>
    <div class="templates-page">
      <div class="templates-header">
        <div class="filter-row">
          <button
            v-for="cat in ['all', ...categories]"
            :key="cat"
            class="cat-btn"
            :class="{ active: activeCategory === cat }"
            @click="activeCategory = cat; load()"
          >{{ cat }}</button>
        </div>
        <span class="count text-muted">{{ templates.length }} templates</span>
      </div>

      <div class="template-grid">
        <div v-for="t in templates" :key="t.slug" class="template-card">
          <router-link :to="`/templates/${t.slug}`" class="tc-link">
            <div class="tc-header">
              <span class="tc-name">{{ t.name }}</span>
              <span :class="`badge badge-${riskBadge(t.risk_level)}`">{{ t.risk_level }}</span>
            </div>
            <p class="tc-desc">{{ t.description }}</p>
            <div class="tc-meta">
              <span class="text-muted">{{ t.tasks.length }} tasks</span>
              <span class="text-muted">~{{ Math.round(t.estimated_duration_seconds / 60) }}m</span>
              <span class="badge badge-neutral">{{ t.category }}</span>
            </div>
            <div class="tc-tags">
              <span v-for="tag in t.tags.slice(0, 4)" :key="tag" class="tag">{{ tag }}</span>
            </div>
          </router-link>

          <div class="tc-actions">
            <button class="btn-run" @click.prevent="openRun(t)" title="Run on hosts">
              ▶ Run
            </button>
            <router-link :to="`/templates/${t.slug}`" class="btn-view">View →</router-link>
          </div>
        </div>
      </div>
    </div>

    <!-- Run modal -->
    <RunTemplateModal
      v-if="runTemplate"
      :template="runTemplate"
      @close="runTemplate = null"
    />
  </AppShell>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import AppShell from '@/components/layout/AppShell.vue'
import RunTemplateModal from '@/components/template-library/RunTemplateModal.vue'
import { templatesApi } from '@/api'
import { useEscapeKey } from '@/composables/useEscapeKey'
import type { Template } from '@/types'

const templates = ref<Template[]>([])
const categories = ref<string[]>([])
const activeCategory = ref('all')
const runTemplate = ref<Template | null>(null)
useEscapeKey(() => { if (runTemplate.value) runTemplate.value = null })

function riskBadge(r: string) {
  return r === 'high' ? 'error' : r === 'medium' ? 'warning' : 'success'
}

function openRun(t: Template) {
  runTemplate.value = t
}

async function load() {
  const resp = await templatesApi.list(activeCategory.value === 'all' ? undefined : activeCategory.value)
  templates.value = resp.data.items
}

onMounted(async () => {
  const cats = await templatesApi.categories()
  categories.value = cats.data
  await load()
})
</script>

<style scoped>
.templates-page { display: flex; flex-direction: column; gap: var(--space-5); }
.templates-header { display: flex; justify-content: space-between; align-items: center; }
.filter-row { display: flex; gap: var(--space-1); flex-wrap: wrap; }
.cat-btn {
  padding: var(--space-1) var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-full);
  background: none;
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
  text-transform: capitalize;
  transition: all var(--transition);
}
.cat-btn.active, .cat-btn:hover { border-color: var(--accent); color: var(--accent); }
.count { font-size: 12px; }

.template-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: var(--space-4);
}

.template-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  display: flex;
  flex-direction: column;
  transition: all var(--transition);
  overflow: hidden;
}
.template-card:hover { border-color: var(--accent); transform: translateY(-2px); box-shadow: var(--shadow-md); }

.tc-link {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
  color: var(--text);
  text-decoration: none;
  flex: 1;
}

.tc-actions {
  display: flex;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4) var(--space-3);
  border-top: 1px solid var(--border);
  background: var(--bg-overlay);
}

.btn-run {
  flex: 1;
  background: var(--success-dim);
  color: var(--success);
  border: 1px solid var(--success);
  border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-3);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  transition: all var(--transition);
}
.btn-run:hover { background: var(--success); color: #000; }

.btn-view {
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  color: var(--text-muted);
  font-size: 12px;
  text-decoration: none;
  transition: all var(--transition);
  white-space: nowrap;
}
.btn-view:hover { color: var(--accent); border-color: var(--accent); }

.tc-header { display: flex; justify-content: space-between; align-items: flex-start; gap: var(--space-2); }
.tc-name { font-weight: 600; font-size: 14px; }
.tc-desc { font-size: 12px; color: var(--text-muted); flex: 1; }
.tc-meta { display: flex; gap: var(--space-3); font-size: 11px; align-items: center; }
.tc-tags { display: flex; flex-wrap: wrap; gap: var(--space-1); }
.tag { background: var(--bg-subtle); color: var(--text-muted); font-size: 11px; padding: 1px 6px; border-radius: var(--radius-full); }
</style>
