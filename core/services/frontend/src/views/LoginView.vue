<template>
  <div class="lg">
    <!-- Zabbix SSO exchange is a single fast round trip (~100ms) — the ENTIRE
         branding block + card is gated off during it, not just the card body,
         so nothing (not even the logo) flashes on a normal embedded page
         transition. Only the plain background shows for that brief window. -->
    <aside v-if="!ssoLoading" class="lg-side">
      <div class="row" style="gap:10px">
        <span class="lg-logo"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 8l4 4-4 4M13 16h5" /></svg></span>
        <b style="font-size:18px;letter-spacing:-.01em">SeyalRun</b>
      </div>
      <div class="lg-head">
        <h1>AI-Powered Automation &amp; Privileged Access</h1>
        <p class="muted">Connect through one audited gateway, run approved playbooks, and answer Zabbix problems from the same console.</p>
      </div>
      <ul class="lg-feat">
        <li v-for="f in FEATURES" :key="f.t" class="row">
          <span class="b nd" :class="f.c" style="height:28px;width:28px;justify-content:center;padding:0"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path v-for="(d, i) in f.d" :key="i" :d="d" /></svg></span>
          <b>{{ f.t }}</b>
        </li>
      </ul>
      <div style="margin-top:auto;font-size:12.5px;color:var(--faint)">{{ appVersion ? 'v' + appVersion + ' · ' : '' }}<a href="https://seyalrun.com/guide/introduction" target="_blank" rel="noopener" style="color:var(--faint)">Documentation</a></div>
    </aside>
    <main v-if="!ssoLoading" class="lg-main">
      <div class="lg-box">
      <div class="card">
        <div class="card-body">
          <template v-if="mfaPending">
            <div class="u-c-text_fw-600_mb-4">Verify your identity</div>
            <div class="u-c-text2_fs-12_mb-16">
              <template v-if="mfaMethod === 'email'">Enter the 6-digit code we emailed you.</template>
              <template v-else>Enter the 6-digit code from your authenticator app.</template>
            </div>
            <div class="form-group">
              <label class="form-label">Code</label>
              <input v-model="mfaCode" class="input" placeholder="123456" inputmode="numeric" autocomplete="one-time-code" maxlength="6" @keydown.enter="doVerifyMfa" autofocus />
            </div>
            <div class="u-c-danger_fs-12_mb-12" v-if="error">{{ error }}</div>
            <button class="btn btn-primary u-jc-center_w-100pc" @click="doVerifyMfa" :disabled="loading">
              {{ loading ? 'Verifying…' : 'Verify' }}
            </button>
            <button v-if="mfaMethod === 'email'" class="btn" style="width:100%;justify-content:center;margin-top:8px" @click="doResend" :disabled="resending">
              {{ resending ? 'Sending…' : 'Resend code' }}
            </button>
          </template>
          <template v-else-if="mfaSetupRequired">
            <div class="u-c-text_fw-600_mb-4">Set up multi-factor authentication</div>
            <div class="u-c-text2_fs-12_mb-16">
              Your group requires MFA before you can continue. Choose a method below.
            </div>
            <div class="fp-toggle-group" style="margin-bottom:14px">
              <button type="button" :class="['fp-toggle', enrollMethod === 'totp' && 'active']" @click="enrollMethod = 'totp'; resetEnroll()">Authenticator App</button>
              <button type="button" :class="['fp-toggle', enrollMethod === 'email' && 'active']" @click="enrollMethod = 'email'; resetEnroll()">Email OTP</button>
            </div>
            <template v-if="enrollMethod === 'totp'">
              <template v-if="!totpSecret">
                <button class="btn btn-primary u-jc-center_w-100pc" :disabled="loading" @click="startTotpSetup">{{ loading ? 'Generating…' : 'Generate QR Code' }}</button>
              </template>
              <template v-else>
                <div style="display:flex;justify-content:center;margin-bottom:10px">
                  <img v-if="qrDataUrl" :src="qrDataUrl" alt="TOTP QR code" style="width:170px;height:170px;border-radius:8px;background:#fff;padding:6px" />
                </div>
                <div class="form-group">
                  <label class="form-label">Code from your app</label>
                  <input v-model="enrollCode" class="input" placeholder="123456" inputmode="numeric" autocomplete="one-time-code" maxlength="6" @keydown.enter="confirmEnroll" />
                </div>
                <div class="u-c-danger_fs-12_mb-12" v-if="error">{{ error }}</div>
                <button class="btn btn-primary u-jc-center_w-100pc" :disabled="saving || !enrollCode" @click="confirmEnroll">{{ saving ? 'Verifying…' : 'Enable & Continue' }}</button>
              </template>
            </template>
            <template v-else>
              <template v-if="!emailCodeSent">
                <button class="btn btn-primary u-jc-center_w-100pc" :disabled="loading" @click="startEmailSetup">{{ loading ? 'Sending…' : 'Send Code to My Email' }}</button>
              </template>
              <template v-else>
                <div class="form-group">
                  <label class="form-label">Code from your email</label>
                  <input v-model="enrollCode" class="input" placeholder="123456" inputmode="numeric" autocomplete="one-time-code" maxlength="6" @keydown.enter="confirmEnroll" />
                </div>
                <div class="u-c-danger_fs-12_mb-12" v-if="error">{{ error }}</div>
                <div class="u-d-flex_gap-8">
                  <button class="btn" :disabled="loading" @click="startEmailSetup">Resend</button>
                  <button class="btn btn-primary" style="flex:1;justify-content:center" :disabled="saving || !enrollCode" @click="confirmEnroll">{{ saving ? 'Verifying…' : 'Enable & Continue' }}</button>
                </div>
              </template>
            </template>
          </template>
          <template v-else-if="showWizardSummary">
            <div class="u-c-text_fw-600_mb-4">Welcome to SeyalRun</div>
            <div class="u-c-text2_fs-12_mb-16">You're all set. Here's a quick summary of your access.</div>
            <div style="font-size:12px;color:var(--text2);margin-bottom:6px">Roles</div>
            <div style="margin-bottom:14px">
              <span v-for="r in (auth.user?.roles || [])" :key="r" class="badge badge-blue" style="margin-right:4px">{{ r }}</span>
              <span class="text-muted-sm" v-if="!(auth.user?.roles || []).length">—</span>
            </div>
            <button class="btn btn-primary u-jc-center_w-100pc" :disabled="loading" @click="doFinishWizard">{{ loading ? 'Finishing…' : 'Get Started' }}</button>
          </template>
          <template v-else-if="mustChange">
            <div class="u-c-text_fw-600_mb-4">Set a new password</div>
            <div class="u-c-text2_fs-12_mb-16">
              You signed in with the default password. Choose a new one (min 8
              characters) before continuing — nothing else works until you do.
            </div>
            <div class="form-group">
              <label class="form-label">New password</label>
              <input v-model="newPassword" type="password" autocomplete="new-password" class="input" placeholder="New password" @keydown.enter="doChangePassword" autofocus />
            </div>
            <div class="form-group">
              <label class="form-label">Confirm new password</label>
              <input v-model="confirmPassword" type="password" autocomplete="new-password" class="input" placeholder="Confirm new password" @keydown.enter="doChangePassword" />
            </div>
            <div class="u-c-danger_fs-12_mb-12" v-if="error">{{ error }}</div>
            <button class="btn btn-primary u-jc-center_w-100pc" @click="doChangePassword" :disabled="loading">
              {{ loading ? 'Saving…' : 'Change Password & Continue' }}
            </button>
          </template>
          <template v-else>
            <div style="margin-bottom:16px"><h2 class="h2" style="font-size:22px">Sign in</h2><p class="sub">Use your SeyalRun account.</p></div>
            <div class="form-group">
              <label class="form-label" for="l-user">Username</label>
              <input id="l-user" v-model="username" type="text" name="username" autocomplete="username" class="input" placeholder="Username" @keydown.enter="doLogin" autofocus />
            </div>
            <div class="form-group">
              <div style="display:flex;justify-content:space-between"><label class="form-label" for="l-pass">Password</label><span class="hint lk" style="margin:0;cursor:help" tabindex="0" title="Ask an administrator to reset your password">Forgot?</span></div>
              <input id="l-pass" v-model="password" type="password" name="password" autocomplete="current-password" class="input" placeholder="Password" @keydown.enter="doLogin" />
            </div>
            <div class="callout" style="--c:var(--bad);font-size:13px;margin-bottom:12px" v-if="error" role="alert">{{ error }}</div>
            <button class="btn btn-primary u-jc-center_w-100pc" style="min-height:40px;font-size:14px" @click="doLogin" :disabled="loading">
              {{ loading ? 'Signing in…' : 'Sign In' }}
            </button>
          </template>
        </div>
      </div>
      <p class="hint" style="text-align:center;margin:14px 0 0">Opened from Zabbix? You are signed in automatically.</p>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import QRCode from 'qrcode'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const appVersion = import.meta.env.VITE_APP_VERSION as string | undefined
const FEATURES = [
  { t: 'Integrations', c: 'info', d: ['M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v4'] },
  { t: 'MCP', c: 'info', d: ['M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z'] },
  { t: 'Playbooks', c: 'ok', d: ['M13 3L4 14h7l-1 7 9-11h-7z'] },
  { t: 'Approvals', c: 'pend', d: ['M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z', 'M8.5 12l2.5 2.5 4.5-5'] },
  { t: 'Audit Controls', c: 'info', d: ['M6 3h9l4 4v14H6z', 'M9 12h7M9 16h7'] },
]
const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)
const saving = ref(false)
const ssoLoading = ref(false)
const mustChange = ref(false)
const newPassword = ref('')
const confirmPassword = ref('')
const mfaPending = ref(false)
const mfaMethod = ref('')
const mfaCode = ref('')
const resending = ref(false)

// Group-forced MFA enrollment (no existing enrollment to verify — must set one up)
const mfaSetupRequired = ref(false)
const enrollMethod = ref<'totp' | 'email'>('totp')
const enrollCode = ref('')
const totpSecret = ref('')
const qrDataUrl = ref('')
const emailCodeSent = ref(false)
function resetEnroll() {
  enrollCode.value = ''
  totpSecret.value = ''
  qrDataUrl.value = ''
  emailCodeSent.value = false
  error.value = ''
}

// One-time first-login "welcome" summary (group policy: setup_wizard)
const showWizardSummary = ref(false)

onMounted(async () => {
  const params = new URLSearchParams(window.location.search)
  const code = params.get('sso_code')
  if (!code) return
  // The code is single-use and dead the moment we attempt it either way — strip it
  // from the address bar now so a later refresh of this tab doesn't resend it (the
  // router guard treats an unconsumed sso_code as always requiring a fresh exchange).
  const url = new URL(window.location.href)
  url.searchParams.delete('sso_code')
  window.history.replaceState(null, '', url.toString())
  ssoLoading.value = true
  try {
    const result = await auth.exchangeSSO(code)
    // Precedence matches api-gateway's own enforcement order (pwc, then mfa_pending,
    // then mfa_setup_required all gate BEFORE anything else) — must_change_password
    // isn't checked here since SSO-provisioned accounts never carry it, but MFA
    // states can co-occur with a normal SSO login.
    if (result.mfaRequired) {
      mfaPending.value = true
      mfaMethod.value = result.mfaMethod || 'totp'
      ssoLoading.value = false
      return
    }
    if (result.mfaSetupRequired) {
      mfaSetupRequired.value = true
      ssoLoading.value = false
      return
    }
    if (result.needsSetupWizard) {
      showWizardSummary.value = true
      ssoLoading.value = false
      return
    }
    // goIn() honors route.query.redirect — the intended page the guard bounced
    // here from (e.g. #/assets from the Zabbix module). Landing everyone on '/'
    // regardless of which SeyalRun page they clicked was the bug: every page
    // inside Zabbix rendered Dashboard.
    goIn()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || 'SSO sign-in failed'
    ssoLoading.value = false
  }
})

// Hash-based routing means the router's `redirect` query lives in
// window.location.hash, never window.location.search — read it via the
// router's own resolved query instead.
function goIn() {
  const redirect = (route.query.redirect as string) || '/'
  router.push(redirect)
}

// A visitor forced here by the router guard specifically to reach a Zabbix
// terminal deep-link (?zbx_host=.. or ?host_id=.., with autoconnect=1) is
// logging in for that one purpose. Extract the raw target so login can bind
// a kiosk claim to it — server-resolved and server-enforced, this is only a
// hint for the backend to attempt resolution, never trusted on its own.
function kioskTargetFromRedirect(): string | undefined {
  const redirect = route.query.redirect as string | undefined
  if (!redirect) return undefined
  const qs = redirect.split('?')[1]
  if (!qs) return undefined
  const params = new URLSearchParams(qs)
  if (params.get('autoconnect') !== '1' && params.get('autoconnect') !== 'true') return undefined
  return params.get('zbx_host') || params.get('host_id') || undefined
}

async function doLogin() {
  // Say why nothing happened. Returning silently left the user clicking a
  // button that made no request and showed no message — indistinguishable
  // from the app being broken.
  if (!username.value || !password.value) {
    error.value = 'Enter your username and password'
    return
  }
  loading.value = true
  error.value = ''
  try {
    const result = await auth.login(username.value, password.value, kioskTargetFromRedirect())
    // Precedence matches api-gateway's own enforcement order: pwc gates BEFORE
    // mfa_pending/mfa_setup_required (a session can carry both claims at once —
    // must_change_password wins until it's cleared, so check it first here too).
    if (auth.user?.must_change_password) {
      mustChange.value = true   // keep password.value in memory as the current password
      return
    }
    if (result.mfaRequired) {
      mfaPending.value = true
      mfaMethod.value = result.mfaMethod || 'totp'
      return
    }
    if (result.mfaSetupRequired) {
      mfaSetupRequired.value = true
      return
    }
    if (result.needsSetupWizard) {
      showWizardSummary.value = true
      return
    }
    goIn()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || 'Invalid credentials'
  } finally {
    loading.value = false
  }
}

async function doChangePassword() {
  if (!newPassword.value) {
    error.value = 'Enter a new password'
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    error.value = 'Passwords do not match'
    return
  }
  loading.value = true
  error.value = ''
  try {
    const result = await auth.changePassword(password.value, newPassword.value, kioskTargetFromRedirect())
    mustChange.value = false
    if (result.mfaRequired) {
      mfaPending.value = true
      mfaMethod.value = result.mfaMethod || 'totp'
      return
    }
    if (result.mfaSetupRequired) {
      mfaSetupRequired.value = true
      return
    }
    if (result.needsSetupWizard) {
      showWizardSummary.value = true
      return
    }
    goIn()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || 'Password change failed'
  } finally {
    loading.value = false
  }
}

async function doVerifyMfa() {
  if (!mfaCode.value) {
    error.value = 'Enter the 6-digit code'
    return
  }
  loading.value = true
  error.value = ''
  try {
    const result = await auth.verifyMfaLogin(mfaCode.value)
    mfaPending.value = false
    if (result.needsSetupWizard) {
      showWizardSummary.value = true
      return
    }
    goIn()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || 'Invalid code'
  } finally {
    loading.value = false
  }
}

async function doResend() {
  resending.value = true
  error.value = ''
  try {
    await auth.resendMfaCode()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || 'Failed to resend code'
  } finally {
    resending.value = false
  }
}

// ── Group-forced MFA enrollment (mfaSetupRequired) ──────────────────────────
async function startTotpSetup() {
  loading.value = true; error.value = ''
  try {
    const { data } = await api.post('/auth/mfa/setup')
    totpSecret.value = data.secret
    qrDataUrl.value = await QRCode.toDataURL(data.otpauth_uri)
  } catch (e: any) { error.value = e?.response?.data?.detail || 'Failed to start setup' }
  finally { loading.value = false }
}

async function startEmailSetup() {
  loading.value = true; error.value = ''
  try {
    await api.post('/auth/mfa/setup-email')
    emailCodeSent.value = true
  } catch (e: any) { error.value = e?.response?.data?.detail || 'Failed to send code' }
  finally { loading.value = false }
}

async function confirmEnroll() {
  saving.value = true; error.value = ''
  try {
    const { data } = await api.post('/auth/mfa/enable', { totp_code: enrollCode.value, method: enrollMethod.value })
    const result = await auth.applyMfaEnableResult(data.access_token, data.user, data.needs_setup_wizard)
    mfaSetupRequired.value = false
    if (result.needsSetupWizard) {
      showWizardSummary.value = true
      return
    }
    goIn()
  } catch (e: any) { error.value = e?.response?.data?.detail || 'Invalid code' }
  finally { saving.value = false }
}

async function doFinishWizard() {
  loading.value = true
  try {
    await auth.completeSetupWizard()
    goIn()
  } catch {
    goIn()  // don't strand the user on a dismiss failure — worst case the wizard shows once more
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
/* Two-panel sign-in (design: Login.dc.html). Stacks to one column on phones. */
.lg { min-height: 100vh; display: flex; flex-wrap: wrap; background: var(--bg); }
.lg-side { flex: 1 1 480px; min-width: 0; background: var(--surface); border-right: 1px solid var(--border);
  padding: 56px max(24px, 5vw); display: flex; flex-direction: column; gap: 36px; }
.lg-logo { width: 36px; height: 36px; border-radius: 9px; background: var(--primary); color: #fff;
  display: inline-flex; align-items: center; justify-content: center; flex: none; }
.lg-head { display: flex; flex-direction: column; gap: 14px; max-width: 460px; }
.lg-head h1 { font-size: 32px; font-weight: 700; letter-spacing: -.02em; line-height: 1.15; margin: 0; }
.lg-head p { margin: 0; font-size: 15px; }
.lg-feat { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 16px; max-width: 460px; }
.lg-feat li { align-items: center; gap: 12px; }
.lg-main { flex: 1 1 480px; min-width: 0; padding: 56px max(24px, 5vw); display: flex; flex-direction: column; justify-content: center; }
.lg-box { width: 100%; max-width: 380px; margin: 0 auto; }
@media (max-width: 960px) {
  .lg-side { border-right: 0; border-bottom: 1px solid var(--border); padding: 28px 20px; gap: 22px; }
  .lg-head h1 { font-size: 26px; }
  .lg-main { padding: 28px 20px 40px; justify-content: flex-start; }
}
.fp-toggle-group { display: flex; background: var(--bg3); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.fp-toggle { flex: 1; padding: 7px 0; font-size: 12px; font-weight: 500; background: transparent; border: none; color: var(--text2); cursor: pointer; }
.fp-toggle.active { background: var(--bg2); color: var(--text); }
</style>
