<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-brand">
        <span class="brand-icon">⬡</span>
        <h1 class="brand-name">SeyalRun Console</h1>
        <p class="brand-sub">Privileged Access Management</p>
      </div>

      <!-- Tab selector -->
      <div class="login-tabs">
        <button :class="{ active: mode === 'creds' }" @click="mode = 'creds'">Username &amp; Password</button>
        <button :class="{ active: mode === 'token' }" @click="mode = 'token'">API Token</button>
      </div>

      <!-- Credentials mode -->
      <form v-if="mode === 'creds'" @submit.prevent="handleCredsLogin" class="login-form">
        <div class="field">
          <label class="field-label">Username</label>
          <input v-model="username" type="text" class="field-input" placeholder="admin" autocomplete="username" :disabled="loading" />
        </div>
        <div class="field">
          <label class="field-label">Password</label>
          <input v-model="password" type="password" class="field-input" placeholder="••••••••" autocomplete="current-password" :disabled="loading" />
        </div>
        <p v-if="error" class="login-error">{{ error }}</p>
        <button type="submit" class="login-btn" :disabled="loading || !username || !password">
          <span v-if="loading">Signing in…</span>
          <span v-else>Sign in</span>
        </button>
      </form>

      <!-- Token mode -->
      <form v-else @submit.prevent="handleTokenLogin" class="login-form">
        <div class="field">
          <label class="field-label">Bearer Token</label>
          <input v-model="token" type="password" class="field-input" placeholder="Paste your session token" autocomplete="off" :disabled="loading" />
          <p class="field-hint">Get a token: JumpServer → User Center → API Key → Create. Or use the Username &amp; Password tab (easier).</p>
        </div>
        <p v-if="error" class="login-error">{{ error }}</p>
        <button type="submit" class="login-btn" :disabled="loading || !token.trim()">
          <span v-if="loading">Authenticating…</span>
          <span v-else>Sign in with Token</span>
        </button>
      </form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import axios from 'axios'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const mode = ref<'creds' | 'token'>('creds')
const username = ref('')
const password = ref('')
const token = ref('')
const error = ref('')
const loading = ref(false)

async function handleCredsLogin() {
  error.value = ''
  loading.value = true
  try {
    // Call studio's server-side login proxy — no CORS issues
    const resp = await axios.post('/api/v1/auth/login', {
      username: username.value,
      password: password.value,
    })
    // Studio returns the token + user data in one shot
    const { token: jmsToken, id, username: uname, name, email } = resp.data
    auth.setUser({ id, username: uname, name, email }, jmsToken)
    const redirect = (route.query.redirect as string) || '/dashboard'
    router.push(redirect)
  } catch (e: unknown) {
    const axErr = e as { response?: { data?: { detail?: string } }; message?: string }
    error.value = axErr?.response?.data?.detail || axErr?.message || 'Login failed. Check credentials.'
  } finally {
    loading.value = false
  }
}

async function handleTokenLogin() {
  error.value = ''
  loading.value = true
  try {
    await auth.login(token.value.trim())
    const redirect = (route.query.redirect as string) || '/dashboard'
    router.push(redirect)
  } catch (e: unknown) {
    const axErr = e as { response?: { data?: { detail?: string } }; message?: string }
    error.value = axErr?.response?.data?.detail
      || axErr?.message
      || 'Invalid or expired token. Use the Username & Password tab instead.'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-base);
  padding: var(--space-6);
}

.login-tabs {
  display: flex;
  gap: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.login-tabs button {
  flex: 1;
  padding: var(--space-2) var(--space-3);
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
  transition: all var(--transition);
}
.login-tabs button.active {
  background: var(--accent-dim);
  color: var(--accent);
}
.login-tabs button:not(:last-child) {
  border-right: 1px solid var(--border);
}

.login-card {
  width: 100%;
  max-width: 400px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-xl);
  padding: var(--space-8);
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  box-shadow: var(--shadow-lg);
}

.login-brand {
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
}

.brand-icon { font-size: 40px; color: var(--accent); }
.brand-name { font-size: 22px; font-weight: 700; color: var(--text); }
.brand-sub  { font-size: 12px; color: var(--text-muted); font-family: var(--font-mono); }

.login-form { display: flex; flex-direction: column; gap: var(--space-4); }

.field { display: flex; flex-direction: column; gap: var(--space-1); }
.field-label { font-size: 12px; font-weight: 600; color: var(--text-muted); }
.field-input {
  background: var(--bg-base);
  border: 1px solid var(--border-muted);
  border-radius: var(--radius-md);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 13px;
  padding: var(--space-3);
  outline: none;
  transition: border-color var(--transition);
}
.field-input:focus { border-color: var(--accent); }

.field-hint { font-size: 11px; color: var(--text-subtle); margin-top: var(--space-1); line-height: 1.5; }

.login-error {
  font-size: 12px;
  color: var(--error);
  background: var(--error-dim);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
}

.login-btn {
  background: var(--accent);
  color: #000;
  border: none;
  border-radius: var(--radius-md);
  padding: var(--space-3);
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  transition: background var(--transition);
}
.login-btn:hover:not(:disabled) { background: var(--accent-hover); }
.login-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.login-hint { font-size: 11px; color: var(--text-muted); text-align: center; }
</style>
