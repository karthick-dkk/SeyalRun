<template>
  <div class="shell">
    <SidebarNav :collapsed="ui.sidebarCollapsed" @toggle="ui.sidebarCollapsed = !ui.sidebarCollapsed" />
    <div class="main" :class="{ 'main--collapsed': ui.sidebarCollapsed }">
      <TopBar />
      <div class="content">
        <slot />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useUiStore } from '@/stores/ui'
import SidebarNav from './SidebarNav.vue'
import TopBar from './TopBar.vue'

const ui = useUiStore()
</script>

<style scoped>
.shell {
  display: flex;
  height: 100vh;
  background: var(--bg-base);
  overflow: hidden;
}

.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  margin-left: var(--sidebar-width);
  transition: margin-left var(--transition);
}

.main--collapsed {
  margin-left: 52px;
}

.content {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-6);
}
</style>
