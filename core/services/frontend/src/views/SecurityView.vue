<template>
  <AppShell>
    <div class="page">
      <div class="ph">
        <div>
          <h1 class="h1">Profile &amp; security</h1>
          <p class="sub">Two-factor sign-in for your account.</p>
        </div>
      </div>

      <div class="sec-col">
        <section class="card">
          <div class="ch">
            <div>
              <h2 class="ct">Two-factor sign-in</h2>
              <p class="sub" style="font-size:12.5px">A code from your phone or email on every sign-in.</p>
            </div>
            <span v-if="status.method" class="b ok">On · {{ status.method === 'totp' ? 'Authenticator app' : 'Email OTP' }}</span>
            <span v-else class="b off">Off</span>
          </div>
          <div class="cb">
            <template v-if="status.method">
              <p class="muted" style="margin:0 0 14px;font-size:13.5px">
                Disabling MFA requires proof of possession — enter a current code
                {{ status.method === 'email' ? '(request one below) ' : '' }}to continue.
              </p>

              <template v-if="!disableStep">
                <button class="btn d" type="button" @click="startDisable">Disable MFA</button>
              </template>
              <template v-else>
                <div v-if="status.method === 'email'" style="margin-bottom:10px">
                  <button class="btn sm" type="button" :disabled="sending" @click="requestDisableCode">
                    {{ sending ? 'Sending…' : (disableCodeSent ? 'Resend code' : 'Send code') }}
                  </button>
                </div>
                <div class="fp-field">
                  <label class="fp-label" for="mfa-dis">Confirmation code</label>
                  <input id="mfa-dis" v-model="disableCode" class="in" placeholder="123456" inputmode="numeric" maxlength="6" autocomplete="one-time-code" />
                </div>
                <div v-if="error" class="callout" style="--c:var(--bad);margin-top:10px" role="alert">{{ error }}</div>
                <div class="acts" style="margin-top:12px">
                  <button class="btn" type="button" @click="disableStep = false; disableCode = ''; error = ''">Cancel</button>
                  <button class="btn p" type="button" :disabled="saving || !disableCode" @click="doDisable">{{ saving ? 'Disabling…' : 'Confirm disable' }}</button>
                </div>
              </template>
            </template>

            <template v-else-if="!auth.can('security.mfa')">
              <p class="muted" style="margin:0;font-size:13.5px">
                Your role does not allow enrolling in MFA. Ask a superadmin to grant the "Allow MFA enrollment" permission on your role.
              </p>
            </template>

            <template v-else>
              <p class="muted" style="margin:0 0 14px;font-size:13.5px">
                MFA is not enabled. Choose a method — an authenticator app (Google
                Authenticator, Authy, ...) or a one-time code emailed to you.
              </p>

              <span class="seg" role="group" aria-label="MFA method" style="margin-bottom:16px">
                <button type="button" :class="{ on: enrollMethod === 'totp' }" @click="enrollMethod = 'totp'; resetEnroll()">Authenticator app</button>
                <button type="button" :class="{ on: enrollMethod === 'email' }" @click="enrollMethod = 'email'; resetEnroll()">Email OTP</button>
              </span>

              <template v-if="enrollMethod === 'totp'">
                <template v-if="!totpSecret">
                  <button class="btn p" type="button" :disabled="loading" @click="startTotpSetup">{{ loading ? 'Generating…' : 'Generate QR code' }}</button>
                </template>
                <template v-else>
                  <div style="display:flex;justify-content:center;margin-bottom:12px">
                    <img v-if="qrDataUrl" :src="qrDataUrl" alt="TOTP QR code" style="width:200px;height:200px;border-radius:8px;background:#fff;padding:8px" />
                  </div>
                  <p class="muted" style="font-size:12.5px;text-align:center;margin:0 0 12px">
                    Can't scan? Enter this secret manually: <code class="mono">{{ totpSecret }}</code>
                  </p>
                  <div class="fp-field">
                    <label class="fp-label" for="mfa-totp">Code from your app</label>
                    <input id="mfa-totp" v-model="enrollCode" class="in" placeholder="123456" inputmode="numeric" maxlength="6" autocomplete="one-time-code" />
                  </div>
                  <div v-if="error" class="callout" style="--c:var(--bad);margin-top:10px" role="alert">{{ error }}</div>
                  <button class="btn p" type="button" style="margin-top:12px" :disabled="saving || !enrollCode" @click="confirmTotp">{{ saving ? 'Verifying…' : 'Enable authenticator' }}</button>
                </template>
              </template>

              <template v-else>
                <template v-if="!emailCodeSent">
                  <button class="btn p" type="button" :disabled="loading" @click="startEmailSetup">{{ loading ? 'Sending…' : 'Send code to my email' }}</button>
                </template>
                <template v-else>
                  <p class="muted" style="font-size:13px;margin:0 0 10px">A code was sent to your account's email address.</p>
                  <div class="fp-field">
                    <label class="fp-label" for="mfa-email">Code from your email</label>
                    <input id="mfa-email" v-model="enrollCode" class="in" placeholder="123456" inputmode="numeric" maxlength="6" autocomplete="one-time-code" />
                  </div>
                  <div v-if="error" class="callout" style="--c:var(--bad);margin-top:10px" role="alert">{{ error }}</div>
                  <div class="acts" style="margin-top:12px">
                    <button class="btn" type="button" :disabled="loading" @click="startEmailSetup">Resend</button>
                    <button class="btn p" type="button" :disabled="saving || !enrollCode" @click="confirmEmail">{{ saving ? 'Verifying…' : 'Enable email OTP' }}</button>
                  </div>
                </template>
              </template>
            </template>
          </div>
        </section>
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
.sec-col { max-width: 640px; }
.fp-field { display: flex; flex-direction: column; gap: 5px; }
.fp-label { font-size: 12.5px; color: var(--muted); font-weight: 500; }
</style>
