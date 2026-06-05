<template>
  <div class="module-picker">
    <div class="picker-header">
      <input
        v-model="search"
        class="search-input"
        placeholder="Search modules…"
        autofocus
      />
      <div class="category-tabs">
        <button
          v-for="cat in ['all', ...categories]"
          :key="cat"
          class="cat-tab"
          :class="{ active: activeCategory === cat }"
          @click="activeCategory = cat"
        >{{ cat }}</button>
      </div>
    </div>
    <div class="module-grid">
      <div
        v-for="mod in filtered"
        :key="mod.name"
        class="module-card"
        @click.stop="$emit('pick', mod)"
      >
        <div class="mod-name">{{ mod.short_name }}</div>
        <div class="mod-full">{{ mod.name }}</div>
        <div class="mod-desc">{{ mod.description }}</div>
        <span class="mod-badge badge badge-info">{{ mod.category }}</span>
      </div>
    </div>
    <div v-if="filtered.length === 0" class="empty">No modules match "{{ search }}"</div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { modulesApi } from '@/api'
import type { ModuleInfo } from '@/types'

defineEmits<{ pick: [module: ModuleInfo] }>()

const search = ref('')
const activeCategory = ref('all')
const modules = ref<ModuleInfo[]>([])
const categories = ref<string[]>([])

onMounted(async () => {
  const [mods, cats] = await Promise.all([
    modulesApi.list(),
    modulesApi.categories(),
  ])
  modules.value = mods.data.items
  categories.value = cats.data
})

const filtered = computed(() => {
  let list = modules.value
  if (activeCategory.value !== 'all') {
    list = list.filter((m) => m.category === activeCategory.value)
  }
  if (search.value.trim()) {
    const q = search.value.toLowerCase()
    list = list.filter(
      (m) => m.name.toLowerCase().includes(q) || m.description.toLowerCase().includes(q)
    )
  }
  return list
})
</script>

<style scoped>
.module-picker {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  max-height: 70vh;
  overflow: hidden;
}

.picker-header {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.search-input {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  background: var(--bg-base);
  border: 1px solid var(--border-muted);
  border-radius: var(--radius-md);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 13px;
  outline: none;
}
.search-input:focus { border-color: var(--accent); }

.category-tabs {
  display: flex;
  gap: var(--space-1);
  flex-wrap: wrap;
}

.cat-tab {
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
.cat-tab.active, .cat-tab:hover { border-color: var(--accent); color: var(--accent); }

.module-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: var(--space-2);
  overflow-y: auto;
  padding-right: var(--space-1);
}

.module-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: var(--space-3);
  cursor: pointer;
  transition: all var(--transition);
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.module-card:hover { border-color: var(--accent); background: var(--accent-dim); }

.mod-name  { font-family: var(--font-mono); font-size: 13px; font-weight: 700; color: var(--text); }
.mod-full  { font-family: var(--font-mono); font-size: 10px; color: var(--text-muted); }
.mod-desc  { font-size: 11px; color: var(--text-muted); margin-top: var(--space-1); flex: 1; }

.empty { text-align: center; color: var(--text-muted); padding: var(--space-8); }
</style>
