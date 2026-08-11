<template>
  <AppShell>
    <div class="page">
    <div class="admin-page">
      <nav class="admin-nav">
        <div class="admin-nav-header">
          <div class="admin-nav-heading">
            <div class="admin-nav-title">Settings</div>
            <div class="admin-nav-subtitle">Zabbix integration, platform health, security, housekeeping and audit logs</div>
          </div>
        </div>

        <template v-for="g in GROUPS" :key="g.label">
          <div v-if="g.tabs.some(t => auth.can(t.area))" class="admin-nav-group">
            <span class="admin-nav-group-label">{{ g.label }}</span>
            <router-link
              v-for="t in g.tabs.filter(t => auth.can(t.area))"
              :key="t.to"
              :to="t.to"
              class="admin-nav-item"
              active-class="active"
              :title="t.label"
            >
              <span class="admin-nav-item-icon" v-html="t.icon" />
              <span class="label">{{ t.label }}</span>
            </router-link>
          </div>
        </template>
      </nav>

      <div class="admin-content">
        <div v-if="activeLabel" class="content-title">{{ activeLabel }}</div>

        <IntegrationAdmin v-if="section === 'integration'" />
        <PlatformSettingsAdmin v-else-if="section === 'platform'" />
        <HealthAdmin v-else-if="section === 'health'" />
        <SecurityAdmin v-else-if="section === 'security'" />
        <HousekeepingAdmin v-else-if="section === 'housekeeping'" />
        <LogBackendAdmin v-else-if="section === 'log-backend'" />
        <MailSettingsAdmin v-else-if="section === 'mail-settings'" />
        <AuditAdmin v-else-if="section === 'audit'" />
      </div>
    </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'
import IntegrationAdmin from './admin/IntegrationAdmin.vue'
import PlatformSettingsAdmin from './admin/PlatformSettingsAdmin.vue'
import HealthAdmin from './admin/HealthAdmin.vue'
import SecurityAdmin from './admin/SecurityAdmin.vue'
import HousekeepingAdmin from './admin/HousekeepingAdmin.vue'
import LogBackendAdmin from './admin/LogBackendAdmin.vue'
import MailSettingsAdmin from './admin/MailSettingsAdmin.vue'
import AuditAdmin from './admin/AuditAdmin.vue'
import { useAuthStore } from '@/stores/auth'
import { groupsFor } from '@/config/adminSections'

const route = useRoute()
const auth = useAuthStore()
const section = computed(() => route.params.section as string)
const isEmbedded = window.self !== window.top

// Same shared registry as AppShell and AdminView. Embedded (Zabbix iframe) mode
// passes `true` so this nav also lists the Admin sections — otherwise an iframe
// user who reached Settings would have no way back, since AppShell hides both
// the sidebar and the topbar there.
const GROUPS = computed(() => groupsFor('settings', isEmbedded))

const activeLabel = computed(() => {
  for (const g of GROUPS.value) {
    const hit = g.tabs.find(t => t.to === route.path)
    if (hit) return hit.label
  }
  return ''
})
</script>
