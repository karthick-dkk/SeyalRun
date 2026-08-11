<template>
  <AppShell>
    <div class="page">
    <div class="admin-page">
      <!-- Only needed inside the Zabbix iframe, where AppShell hides its own sidebar
           entirely and this is the sole nav. Standalone now has one unified nav (an
           expandable Admin tree in AppShell's own sidebar) — rendering this one too
           would just be the same 13 destinations shown twice again. -->
      <nav v-if="isEmbedded" class="admin-nav" :class="{ collapsed: navCollapsed }">
        <div class="admin-nav-header">
          <div v-if="!navCollapsed" class="admin-nav-heading">
            <div class="admin-nav-title">Admin</div>
            <div class="admin-nav-subtitle">Users, authorizations, credentials, zones, security policies and audit logs</div>
          </div>
          <button class="admin-nav-collapse" @click="navCollapsed = !navCollapsed" :title="navCollapsed ? 'Expand' : 'Collapse'">
            <span v-html="navCollapsed ? ICONS.chevronRight : ICONS.chevronLeft" />
          </button>
        </div>

        <template v-for="g in GROUPS" :key="g.label">
          <div v-if="g.tabs.some(t => auth.can(t.area))" class="admin-nav-group">
            <span v-if="!navCollapsed" class="admin-nav-group-label">{{ g.label }}</span>
            <router-link
              v-for="t in g.tabs.filter(t => auth.can(t.area))"
              :key="t.to"
              :to="t.to"
              class="admin-nav-item"
              active-class="active"
              :title="t.label"
            >
              <span class="admin-nav-item-icon" v-html="t.icon" />
              <span v-if="!navCollapsed" class="label">{{ t.label }}</span>
            </router-link>
          </div>
        </template>
      </nav>

      <div class="admin-content">
        <div v-if="activeLabel" class="content-title">{{ activeLabel }}</div>

        <UsersAdmin v-if="section === 'users'" />
        <RolesAdmin v-else-if="section === 'roles'" />
        <AuthorizationsAdmin v-else-if="section === 'authorizations'" />
        <CredentialsAdmin v-else-if="section === 'credentials'" />
        <ZonesAdmin v-else-if="section === 'zones'" />
        <TriggerBindingsAdmin v-else-if="section === 'trigger-bindings'" />
      </div>
    </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'
import UsersAdmin from './admin/UsersAdmin.vue'
import RolesAdmin from './admin/RolesAdmin.vue'
import AuthorizationsAdmin from './admin/AuthorizationsAdmin.vue'
import CredentialsAdmin from './admin/CredentialsAdmin.vue'
import ZonesAdmin from './admin/ZonesAdmin.vue'
import TriggerBindingsAdmin from './admin/TriggerBindingsAdmin.vue'
import { useAuthStore } from '@/stores/auth'
import { groupsFor, ICONS } from '@/config/adminSections'

const route = useRoute()
const auth = useAuthStore()
const section = computed(() => route.params.section as string)

// Same technique AppShell.vue's own sidebar uses to detect the Zabbix
// iframe — start collapsed there (the iframe is narrower than a full
// browser window), expanded everywhere else. Either way it's just the
// initial state; the toggle below still works in both contexts.
const isEmbedded = window.self !== window.top
const navCollapsed = ref(isEmbedded)

// Sections, labels, icons and paths all come from the shared registry — this
// file used to carry its own copy alongside AppShell's and SettingsView's, and
// the three had drifted. Embedded (Zabbix iframe) mode passes `true` so this
// nav lists the Settings sections too: AppShell hides both its sidebar and its
// topbar there, so this is the only navigation an iframe user has.
const GROUPS = computed(() => groupsFor('admin', isEmbedded))

const activeLabel = computed(() => {
  for (const g of GROUPS.value) {
    const hit = g.tabs.find(t => t.to === route.path)
    if (hit) return hit.label
  }
  return ''
})
</script>

