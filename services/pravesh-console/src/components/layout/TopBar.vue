<template>
  <header class="topbar">
    <div class="topbar-left">
      <h1 class="page-title">{{ pageTitle }}</h1>
    </div>
    <div class="topbar-right">
      <!-- Terminal button — opens in new window -->
      <a
        href="/terminal"
        target="_blank"
        rel="noopener"
        class="terminal-btn"
        title="Open SSH Terminal in new window"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="4 17 10 11 4 5" />
          <line x1="12" y1="19" x2="20" y2="19" />
        </svg>
        Terminal ↗
      </a>

      <div class="divider" />

      <a
        href="http://192.168.64.2"
        target="_blank"
        rel="noopener"
        class="jms-btn"
        title="Open JumpServer Console"
      >
        <span class="jms-icon">⬡</span>
        JumpServer
        <span class="jms-arrow">↗</span>
      </a>

    </div>
  </header>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

const pageTitle = computed(() => {
  const map: Record<string, string> = {
    Dashboard: 'Dashboard',
    Playbooks: 'Playbooks',
    NewPlaybook: 'New Playbook',
    EditPlaybook: 'Edit Playbook',
    Templates: 'Template Library',
    TemplateDetail: 'Template',
    Jobs: 'Jobs',
    JobDetail: 'Job Detail',
    Assets: 'Assets',
    Sessions: 'Sessions',
    SSHTerminal: 'Terminal',
    Alerts: 'Alerts',
    Settings: 'Settings',
  }
  return map[route.name as string] ?? 'SeyalRun'
})
</script>

<style scoped>
.topbar {
  height: var(--topbar-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--space-6);
  border-bottom: 1px solid var(--border);
  background: var(--bg-surface);
  flex-shrink: 0;
}

.page-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.terminal-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: var(--space-1) var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 500;
  text-decoration: none;
  transition: all var(--transition);
  background: var(--bg-overlay);
}
.terminal-btn:hover,
.terminal-btn.router-link-active {
  border-color: var(--success);
  color: var(--success);
  background: var(--success-dim);
}

.jms-btn {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-1) var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 500;
  text-decoration: none;
  transition: all var(--transition);
  background: var(--bg-overlay);
}
.jms-btn:hover {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-dim);
}
.jms-icon { font-size: 13px; }
.jms-arrow { font-size: 10px; opacity: 0.6; }

.divider {
  width: 1px;
  height: 16px;
  background: var(--border);
}

</style>
