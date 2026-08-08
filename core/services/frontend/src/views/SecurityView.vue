<template>
  <AppShell>
    <div class="page">
      <div class="card" style="max-width:560px;margin:0 auto">
        <div class="card-header">Security — Multi-Factor Authentication</div>
        <div style="padding:18px">
          <template v-if="status.method">
            <div class="mfa-status">
              <span class="badge badge-green">● MFA enabled — {{ status.method === 'totp' ? 'Authenticator app' : 'Email OTP' }}</span>
            </div>
            <div style="font-size:12px;color:var(--text2);margin:10px 0 16px">
              Disabling MFA requires proof of possession — enter a current code
              {{ status.method === 'email' ? '(request one below) ' : '' }}to continue.
            </div>

            <template v-if="!disableStep">
              <button class="btn" @click="startDisable">Disable MFA</button>
            </template>
            <template v-else>
              <div v-if="status.method === 'email'" style="margin-bottom:10px">
                <button class="btn btn-sm" :disabled="sending" @click="requestDisableCode">
                  {{ sending ? 'Sending…' : (disableCodeSent ? 'Resend code' : 'Send code') }}
                </button>
              </div>
              <div class="fp-field">
                <label class="fp-label">Confirmation code</label>
                <input v-model="disableCode" class="fp-input" placeholder="123456" inputmode="numeric" maxlength="6" />
              </div>
              <div v-if="error" class="fp-error">{{ error }}</div>
              <div style="display:flex;gap:8px;margin-top:10px">
                <button class="btn" @click="disableStep = false; disableCode = ''; error = ''">Cancel</button>
                <button class="btn btn-primary" :disabled="saving || !disableCode" @click="doDisable">{{ saving ? 'Disabling…' : 'Confirm Disable' }}</button>
              </div>
            </template>
          </template>

          <template v-else-if="!auth.can('security.mfa')">
            <div style="font-size:13px;color:var(--text2)">
              Your role does not allow enrolling in MFA. Ask a superadmin to grant the "Allow MFA enrollment" permission on your role.
            </div>
          </template>

          <template v-else>
            <div style="font-size:12px;color:var(--text2);margin-bottom:16px">
              MFA is not enabled. Choose a method — an authenticator app (Google
              Authenticator, Authy, ...) or a one-time code emailed to you.
            </div>

            <div class="fp-toggle-group" style="margin-bottom:16px">
              <button :class="['fp-toggle', enrollMethod === 'totp' && 'active']" @click="enrollMethod = 'totp'; resetEnroll()">Authenticator App</button>
              <button :class="['fp-toggle', enrollMethod === 'email' && 'active']" @click="enrollMethod = 'email'; resetEnroll()">Email OTP</button>
            </div>

            <template v-if="enrollMethod === 'totp'">
              <template v-if="!totpSecret">
                <button class="btn btn-primary" :disabled="loading" @click="startTotpSetup">{{ loading ? 'Generating…' : 'Generate QR Code' }}</button>
              </template>
              <template v-else>
                <div style="display:flex;justify-content:center;margin-bottom:12px">
                  <img v-if="qrDataUrl" :src="qrDataUrl" alt="TOTP QR code" style="width:200px;height:200px;border-radius:8px;background:#fff;padding:8px" />
                </div>
                <div style="font-size:11px;color:var(--text2);text-align:center;margin-bottom:12px">
                  Can't scan? Enter this secret manually: <code>{{ totpSecret }}</code>
                </div>
                <div class="fp-field">
                  <label class="fp-label">Code from your app</label>
                  <input v-model="enrollCode" class="fp-input" placeholder="123456" inputmode="numeric" maxlength="6" />
                </div>
                <div v-if="error" class="fp-error">{{ error }}</div>
                <button class="btn btn-primary" style="margin-top:10px" :disabled="saving || !enrollCode" @click="confirmTotp">{{ saving ? 'Verifying…' : 'Enable Authenticator' }}</button>
              </template>
            </template>

            <template v-else>
              <template v-if="!emailCodeSent">
                <button class="btn btn-primary" :disabled="loading" @click="startEmailSetup">{{ loading ? 'Sending…' : 'Send Code to My Email' }}</button>
              </template>
              <template v-else>
                <div style="font-size:12px;color:var(--text2);margin-bottom:10px">A code was sent to your account's email address.</div>
                <div class="fp-field">
                  <label class="fp-label">Code from your email</label>
                  <input v-model="enrollCode" class="fp-input" placeholder="123456" inputmode="numeric" maxlength="6" />
                </div>
                <div v-if="error" class="fp-error">{{ error }}</div>
                <div style="display:flex;gap:8px;margin-top:10px">
                  <button class="btn" :disabled="loading" @click="startEmailSetup">Resend</button>
                  <button class="btn btn-primary" :disabled="saving || !enrollCode" @click="confirmEmail">{{ saving ? 'Verifying…' : 'Enable Email OTP' }}</button>
                </div>
              </template>
            </template>
          </template>
        </div>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import QRCode from 'qrcode'
import api from '@/api/client'
import AppShell from '@/components/layout/AppShell.vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

const status = reactive<{ method: string | null }>({ method: null })
const loading = ref(false)
const saving = ref(false)
const sending = ref(false)
const error = ref('')

const enrollMethod = ref<'totp' | 'email'>('totp')
const enrollCode = ref('')
const totpSecret = ref('')
const qrDataUrl = ref('')
const emailCodeSent = ref(false)

const disableStep = ref(false)
const disableCode = ref('')
const disableCodeSent = ref(false)

function resetEnroll() {
  enrollCode.value = ''
  totpSecret.value = ''
  qrDataUrl.value = ''
  emailCodeSent.value = false
  error.value = ''
}

async function loadStatus() {
  try {
    const { data } = await api.get('/auth/mfa/status')
    status.method = data.method || null
  } catch { /* leave default */ }
}

async function startTotpSetup() {
  loading.value = true; error.value = ''
  try {
    const { data } = await api.post('/auth/mfa/setup')
    totpSecret.value = data.secret
    qrDataUrl.value = await QRCode.toDataURL(data.otpauth_uri)
  } catch (e: any) { error.value = e?.response?.data?.detail || 'Failed to start setup' }
  finally { loading.value = false }
}

async function confirmTotp() {
  saving.value = true; error.value = ''
  try {
    const { data } = await api.post('/auth/mfa/enable', { totp_code: enrollCode.value, method: 'totp' })
    await auth.applyMfaEnableResult(data.access_token, data.user, data.needs_setup_wizard)
    status.method = 'totp'
    resetEnroll()
  } catch (e: any) { error.value = e?.response?.data?.detail || 'Invalid code' }
  finally { saving.value = false }
}

async function startEmailSetup() {
  loading.value = true; error.value = ''
  try {
    await api.post('/auth/mfa/setup-email')
    emailCodeSent.value = true
  } catch (e: any) { error.value = e?.response?.data?.detail || 'Failed to send code' }
  finally { loading.value = false }
}

async function confirmEmail() {
  saving.value = true; error.value = ''
  try {
    const { data } = await api.post('/auth/mfa/enable', { totp_code: enrollCode.value, method: 'email' })
    await auth.applyMfaEnableResult(data.access_token, data.user, data.needs_setup_wizard)
    status.method = 'email'
    resetEnroll()
  } catch (e: any) { error.value = e?.response?.data?.detail || 'Invalid code' }
  finally { saving.value = false }
}

function startDisable() {
  disableStep.value = true
  disableCode.value = ''
  disableCodeSent.value = false
  error.value = ''
}

async function requestDisableCode() {
  sending.value = true; error.value = ''
  try {
    await api.post('/auth/mfa/disable/request-code')
    disableCodeSent.value = true
  } catch (e: any) { error.value = e?.response?.data?.detail || 'Failed to send code' }
  finally { sending.value = false }
}

async function doDisable() {
  saving.value = true; error.value = ''
  try {
    await api.post('/auth/mfa/disable', { totp_code: disableCode.value })
    status.method = null
    if (auth.user) auth.user.mfa_method = null
    disableStep.value = false
    disableCode.value = ''
  } catch (e: any) { error.value = e?.response?.data?.detail || 'Invalid code' }
  finally { saving.value = false }
}

onMounted(loadStatus)
</script>

<style scoped>
.mfa-status { margin-bottom: 4px; }
.fp-field { display: flex; flex-direction: column; gap: 5px; margin-top: 10px; }
.fp-label { font-size: 12px; color: var(--text2); font-weight: 500; }
.fp-input { padding: 7px 10px; background: var(--bg3); border: 1px solid var(--border); border-radius: 5px; color: var(--text); font-size: 13px; outline: none; width: 100%; box-sizing: border-box; }
.fp-input:focus { border-color: var(--accent2); }
.fp-error { font-size: 12px; color: var(--danger); padding: 8px 0; }
.fp-toggle-group { display: flex; background: var(--bg3); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.fp-toggle { flex: 1; padding: 7px 0; font-size: 12px; font-weight: 500; background: transparent; border: none; color: var(--text2); cursor: pointer; }
.fp-toggle.active { background: var(--bg2); color: var(--text); }
</style>
