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

const route = useRoute()
const auth = useAuthStore()
const section = computed(() => route.params.section as string)

function _svg(body: string): string {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`
}
const ICONS = {
  link:        _svg('<path d="M13.5 10.5 21 3m0 0h-5.25M21 3v5.25M10.5 13.5 3 21m0 0h5.25M3 21v-5.25"/>'),
  gear:        _svg('<path d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065zM15 12a3 3 0 11-6 0 3 3 0 016 0z"/>'),
  pulse:       _svg('<path d="M2.25 12h3l2.25-7.5 4.5 15L14.25 9l1.5 3h6"/>'),
  shieldCheck: _svg('<path d="M12 2.25 4.5 5.063v6.562a8.25 8.25 0 007.5 8.25 8.25 8.25 0 007.5-8.25V5.063L12 2.25z"/><path d="m9 12.75 2.25 2.25 3.75-5.25"/>'),
  trash:       _svg('<path d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0"/>'),
  archive:     _svg('<path d="M20.25 7.5l-.625 10.632a2.25 2.25 0 01-2.247 2.118H6.622a2.25 2.25 0 01-2.247-2.118L3.75 7.5M10 11.25h4M3.375 7.5h17.25c.621 0 1.125-.504 1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125z"/>'),
  mail:        _svg('<path d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25H4.5a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75"/>'),
  list:        _svg('<path d="M8.25 6.75h12M8.25 12h12m-12 5.25h12M3.75 6.75h.007v.008H3.75V6.75zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zM3.75 12h.007v.008H3.75V12zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm-.375 5.25h.007v.008H3.75v-.008zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0z"/>'),
}

// Moved out of Admin's own nav into this dedicated Settings page (reached via the
// topbar gear icon) — same underlying view components and RBAC areas as before
// (admin.integration, admin.platform, ...), just a different route prefix and a
// nav grouping suited to a smaller, settings-focused set of pages.
const GROUPS = [
  { label: 'Integration', tabs: [
    { area: 'admin.integration', to: '/settings/integration', label: 'Integration', icon: ICONS.link },
    { area: 'admin.platform', to: '/settings/platform', label: 'SeyalRun Settings', icon: ICONS.gear },
  ] },
  { label: 'Platform', tabs: [
    { area: 'admin.health', to: '/settings/health', label: 'Health', icon: ICONS.pulse },
    { area: 'admin.security', to: '/settings/security', label: 'Security', icon: ICONS.shieldCheck },
    { area: 'admin.housekeeping', to: '/settings/housekeeping', label: 'Housekeeping', icon: ICONS.trash },
    { area: 'admin.log-backend', to: '/settings/log-backend', label: 'Log Backend', icon: ICONS.archive },
    { area: 'admin.mail-settings', to: '/settings/mail-settings', label: 'Mail Settings', icon: ICONS.mail },
    { area: 'admin.audit', to: '/settings/audit', label: 'Audit Logs', icon: ICONS.list },
  ] },
]

const activeLabel = computed(() => {
  for (const g of GROUPS) {
    const hit = g.tabs.find(t => t.to === route.path)
    if (hit) return hit.label
  }
  return ''
})
</script>
