<template>
  <aside class="sidebar" :class="{ 'sidebar--collapsed': collapsed }">
    <div class="sidebar-header">
      <div class="logo">
        <span class="logo-icon">⬡</span>
        <span v-if="!collapsed" class="logo-text">SeyalRun</span>
      </div>
      <button class="collapse-btn" @click="$emit('toggle')" :title="collapsed ? 'Expand' : 'Collapse'">
        {{ collapsed ? '›' : '‹' }}
      </button>
    </div>

    <nav class="nav">
      <router-link
        v-for="item in navItems"
        :key="item.to"
        :to="item.to"
        class="nav-item"
        :title="collapsed ? item.label : ''"
      >
        <span class="nav-icon">{{ item.icon }}</span>
        <span v-if="!collapsed" class="nav-label">{{ item.label }}</span>
      </router-link>
    </nav>

    <div class="sidebar-footer" v-if="!collapsed">
      <div class="user-info">
        <div class="user-avatar">{{ auth.user?.username?.charAt(0).toUpperCase() }}</div>
        <div class="user-details">
          <div class="user-name">{{ auth.user?.name || auth.user?.username }}</div>
          <div class="user-email">{{ auth.user?.email }}</div>
        </div>
      </div>
      <button class="logout-btn" @click="auth.logout(); router.push('/login')">Logout</button>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

defineProps<{ collapsed: boolean }>()
defineEmits<{ toggle: [] }>()

const router = useRouter()
const auth = useAuthStore()

const navItems = [
  { to: '/dashboard',  icon: '◈', label: 'Dashboard' },
  { to: '/assets',     icon: '⊛', label: 'Assets' },
  { to: '/sessions',   icon: '⊙', label: 'Sessions' },
  { to: '/playbooks',  icon: '⬡', label: 'Playbooks' },
  { to: '/templates',  icon: '⊞', label: 'Templates' },
  { to: '/jobs',       icon: '◫', label: 'Jobs' },
  { to: '/alerts',     icon: '◉', label: 'Alerts' },
  { to: '/settings',   icon: '⚙', label: 'Settings' },
]
</script>

<style scoped>
.sidebar {
  position: fixed;
  top: 0;
  left: 0;
  height: 100vh;
  width: var(--sidebar-width);
  background: var(--bg-surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  transition: width var(--transition);
  z-index: 100;
}

.sidebar--collapsed {
  width: 52px;
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-4) var(--space-3);
  border-bottom: 1px solid var(--border);
  height: var(--topbar-height);
}

.logo {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-family: var(--font-mono);
  font-weight: 700;
  color: var(--accent);
  font-size: 15px;
}

.logo-icon { font-size: 18px; }

.collapse-btn {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 16px;
  padding: 2px 4px;
  border-radius: var(--radius-sm);
  transition: color var(--transition), background var(--transition);
}
.collapse-btn:hover { color: var(--text); background: var(--bg-subtle); }

.nav {
  flex: 1;
  padding: var(--space-3) var(--space-2);
  display: flex;
  flex-direction: column;
  gap: 2px;
  overflow-y: auto;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 500;
  transition: all var(--transition);
  white-space: nowrap;
}
.nav-item:hover {
  background: var(--bg-subtle);
  color: var(--text);
}
.nav-item.router-link-active {
  background: var(--accent-dim);
  color: var(--accent);
}

.nav-icon { font-size: 15px; flex-shrink: 0; }
.nav-label { overflow: hidden; text-overflow: ellipsis; }

.sidebar-footer {
  padding: var(--space-3);
  border-top: 1px solid var(--border);
}

.user-info {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}

.user-avatar {
  width: 28px;
  height: 28px;
  border-radius: var(--radius-full);
  background: var(--accent-dim);
  color: var(--accent);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}

.user-details { min-width: 0; }
.user-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.user-email {
  font-size: 11px;
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.logout-btn {
  width: 100%;
  padding: var(--space-1) var(--space-2);
  background: none;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  color: var(--text-muted);
  font-size: 11px;
  cursor: pointer;
  transition: all var(--transition);
}
.logout-btn:hover { border-color: var(--error); color: var(--error); }
</style>
