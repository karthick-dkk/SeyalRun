<template>
  <div>
    <div class="card">
      <div class="card-header">JumpServer delegated login</div>
      <div class="u-p-18">
        <div class="int-grid">
          <div class="int-row">
            <span class="int-k">Provider</span>
            <span class="badge" :class="idp ? (idp.enabled ? 'badge-green' : 'badge-gray') : 'badge-red'">
              {{ idp ? (idp.enabled ? 'Loaded · enabled' : 'Loaded · disabled') : 'Not loaded' }}
            </span>
          </div>
          <div class="int-row">
            <span class="int-k">Endpoint</span>
            <span class="ip-mono">POST /api/v1/auth/jumpserver-login</span>
          </div>
          <div class="int-row">
            <span class="int-k">Configured by</span>
            <span style="font-size:13px"><code>jumpserver_api_url</code> in identity-service</span>
          </div>
        </div>

        <p class="note">
          With <code>jumpserver_api_url</code> blank the provider is inert: it returns "not me" and login
          falls through to the other providers, so a deployment that does not front a JumpServer is
          unaffected. When it is set, a user's JumpServer access token is validated against JumpServer's
          own API and the user is auto-provisioned here.
        </p>

        <table class="role-map">
          <thead><tr><th>JumpServer profile</th><th>SeyalRun role</th></tr></thead>
          <tbody>
            <tr><td><code>is_superuser</code></td><td>superadmin</td></tr>
            <tr><td>org admin, or a system role named “…admin…”</td><td>admin</td></tr>
            <tr><td>everyone else</td><td>user</td></tr>
          </tbody>
        </table>

        <p class="note">
          JumpServer stays the source of truth for <em>identity</em>; SeyalRun remains the source of truth
          for <em>access</em> — a delegated user still needs an authorization before they can reach a host.
          TLS verification is always on; <code>jumpserver_ca_bundle</code> only supplies a trust root for a
          self-signed JumpServer and is never a way to turn verification off.
        </p>
      </div>
    </div>

    <PluginList
      class="u-mt-16"
      title="JumpServer plugins"
      description="The identity provider signs JumpServer users in. A session broker would hand live SSH session establishment to JumpServer instead of SeyalRun's native path — the extension point exists; no broker ships today, so sessions are brokered natively."
      empty-text="No JumpServer plugins loaded in this deployment."
      :axes="['idp', 'brokers']"
      :only="['jumpserver', 'jumpserver_broker']"
      :plugins="plugins"
      :can-toggle="auth.isSuperAdmin"
      @changed="$emit('changed')"
    />

    <div class="card u-mt-16">
      <div class="card-header">Session brokering</div>
      <div class="u-p-18">
        <p class="note u-mt-0">
          Sessions are currently brokered <strong>natively</strong>: terminal-service opens the SSH
          connection itself, unwrapping the credential with its own service identity, and records the
          session. Installing a session-broker plugin would delegate that to JumpServer per host — the
          axis is discovered at startup and appears above the moment such a plugin ships in the image.
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useAuthStore } from '@/stores/auth'
import PluginList from '@/components/integration/PluginList.vue'

const props = defineProps<{ plugins: any[] }>()
defineEmits<{ (e: 'changed'): void }>()

const auth = useAuthStore()
const idp = computed(() => (props.plugins || []).find(p => p.axis === 'idp' && p.name === 'jumpserver'))
</script>

<style scoped>
.int-grid { display: flex; flex-direction: column; gap: 12px; }
.int-row { display: flex; align-items: center; gap: 12px; font-size: 14px; }
.int-k { flex: 0 0 130px; color: var(--text2); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }
.note { color: var(--text2); font-size: 12px; line-height: 1.6; margin-top: 16px; }
.u-mt-0 { margin-top: 0; }
code { background: var(--bg3); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
.role-map { width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 12px; }
.role-map th { text-align: left; color: var(--text2); font-weight: 500; padding: 6px 8px; border-bottom: 1px solid var(--border); }
.role-map td { padding: 6px 8px; border-bottom: 1px solid var(--border); color: var(--text); }
</style>
