<template>
  <AppShell>
    <div class="page">
      <div class="page-header">
        <div>
          <div class="page-title">Zones</div>
          <div class="page-subtitle">Network zones and their SSH gateways — nest a zone under a parent to build a multi-hop ProxyJump chain</div>
        </div>
      </div>
      <ZonesAdmin />
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import AppShell from '@/components/layout/AppShell.vue'
import ZonesAdmin from './admin/ZonesAdmin.vue'
</script>
