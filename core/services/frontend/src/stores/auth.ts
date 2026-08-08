import { defineStore } from 'pinia'
import api, { getToken, setToken, clearToken } from '@/api/client'

// App.vue's boot-time onMounted and the router's initial beforeEach guard both
// call init() within the same tick (main.ts mounts without awaiting
// router.isReady()). Without this guard they fire two independent
// /auth/session requests; if the first is still in flight when a user lands
// on a deep link, gets bounced to /login, and logs in before it resolves, its
// stale "not authenticated" response can land after login and wipe the fresh
// session. Sharing one in-flight promise makes concurrent callers resolve
// together, closing that window.
let _initPromise: Promise<void> | null = null

export interface SessionUser {
  id: string
  username: string
  display_name?: string
  email?: string | null
  role_id?: string
  role_name: string
  roles?: string[]
  is_active?: boolean
  must_change_password?: boolean
  created_at?: string
  // Server-asserted, signed into the JWT at login (see identity-service auth.py) —
  // never client-inferred. Present only for a login forced through a Zabbix
  // terminal deep-link; absent for every ordinary login.
  kiosk?: boolean
  kiosk_host_id?: string | null
  mfa_method?: string | null
}

// Frontend mirror of the gateway RBAC matrix — used only to hide nav/tabs the
// caller can't use before the authoritative gateway permissions load (or if
// that fetch fails). The api-gateway is the real, authoritative enforcement —
// keep this in sync with libs/rbaccore.BUILTIN_ROLE_PERMS + api-gateway
// rbac.py::_NAV_SEGMENTS, not the other way around.
const ALL = '*'
const AREA_ROLES: Record<string, string[]> = {
  dashboard:                ['superadmin', 'admin', 'support'],
  hosts:                    ['superadmin', 'admin', 'support', 'user'],
  assets:                   ['superadmin', 'admin', 'support'],
  zones:                    ['superadmin', 'admin'],
  terminal:                 ['superadmin', 'admin', 'support', 'user'],
  sessions:                 ['superadmin', 'admin', 'support', 'user'],
  recordings:               ['superadmin', 'admin', 'support', 'user'],
  jobs:                     ['superadmin', 'admin', 'support', 'user'],
  automation:               ['superadmin', 'admin', 'support', 'user'],
  'admin.users':            ['superadmin', 'admin', 'support'],
  'admin.roles':            ['superadmin', 'admin', 'support'],
  'admin.authorizations':   ['superadmin', 'admin', 'support'],
  'admin.credentials':      ['superadmin', 'admin', 'support'],
  'admin.zones':            ['superadmin', 'admin'],
  'admin.security':         ['superadmin'],
  'admin.automation':       ['superadmin', 'admin', 'support'],
  'admin.zabbix-integration': ['superadmin', 'admin', 'support'],
  'admin.integration':      ['superadmin'],
  'admin.platform':         ['superadmin'],
  'admin.health':           ['superadmin', 'admin', 'support'],
  'admin.housekeeping':     ['superadmin'],
  'admin.log-backend':      ['superadmin'],
  'admin.mail-settings':    ['superadmin'],
  'admin.audit':            ['superadmin', 'admin'],
  'security.mfa':           ['superadmin', 'admin'],
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null as SessionUser | null,
    ready: false,
    // Authoritative per-area access from the gateway (handles custom roles); the static
    // AREA_ROLES map is only a fallback used before this loads.
    nav: {} as Record<string, boolean>,
    navReady: false,
  }),

  getters: {
    isAuthenticated: (state) => !!state.user,
    roles: (state): string[] => state.user?.roles?.length ? state.user.roles : (state.user?.role_name ? [state.user.role_name] : []),
    isAdmin: (state) => {
      const r = state.user?.roles?.length ? state.user.roles : [state.user?.role_name]
      return r.includes('admin') || r.includes('superadmin')
    },
    isSuperAdmin: (state) => (state.user?.roles || [state.user?.role_name]).includes('superadmin'),
    // support has full inventory/credential/playbook CRUD capability (backend-enforced via
    // libs.rbaccore) but is not "admin" — use this, not isAdmin, for asset/credential/playbook
    // management UI so support's write buttons actually appear.
    isAdminOrSupport(): boolean {
      return this.isAdmin || this.roles.includes('support')
    },
    // Server-asserted (signed JWT claim, see _applyToken) — a login forced through a
    // Zabbix terminal deep-link. Never set by anything client-side.
    isKiosk: (state) => !!state.user?.kiosk,
    kioskHostId: (state) => state.user?.kiosk_host_id ?? null,
  },

  actions: {
    /** Can the current user see/use a named area (nav item or admin.<section>). */
    can(area: string): boolean {
      // Prefer the authoritative permissions fetched from the gateway.
      if (this.navReady && area in this.nav) return !!this.nav[area]
      // Fallback (gateway perms not loaded yet / fetch failed). Deny areas we have no
      // explicit rule for — never assume access, so a forbidden page can't slip through.
      const allowed = AREA_ROLES[area]
      if (!allowed) return false
      if (allowed[0] === ALL) return true
      return this.roles.some((r) => allowed.includes(r))
    },
    /** Any admin tab visible at all? */
    canAnyAdmin(): boolean {
      return Object.keys(AREA_ROLES).filter((a) => a.startsWith('admin.')).some((a) => this.can(a))
    },

    async init() {
      if (_initPromise) return _initPromise
      _initPromise = this._doInit()
      try {
        await _initPromise
      } finally {
        _initPromise = null
      }
    },

    async _doInit() {
      // Always ask, even with no in-memory token: if one exists it rides as
      // the normal Authorization header (axios interceptor); if not, the
      // browser may still carry the httpOnly sr_session bootstrap cookie
      // from an earlier direct login (e.g. this is a brand-new tab opened
      // from a Zabbix "Terminal" link, sharing no JS memory with an
      // already-logged-in console tab). The server tells us which, if
      // either, applies — nothing is ever decoded client-side.
      try {
        const { data } = await api.get('/auth/session')
        if (data.access_token) setToken(data.access_token)
        this.user = {
          id: data.id,
          username: data.username,
          role_name: data.role_name,
          roles: data.roles,
          must_change_password: data.must_change_password,
          kiosk: data.kiosk,
          kiosk_host_id: data.kiosk_host_id,
        }
      } catch {
        clearToken()
        this.user = null
      }
      if (this.user) await this.loadNav()
      this.ready = true
    },

    async loadNav() {
      try {
        const { data } = await api.get('/auth/nav')
        this.nav = data || {}
        this.navReady = true
      } catch {
        this.nav = {}
        this.navReady = false
      }
    },

    // Apply a freshly-issued token. The token is opaque (nothing to decode) —
    // kiosk/kiosk_host_id now ride directly in the response body's `user`
    // object, populated server-side (see identity-service auth.py::_user_out).
    _applyToken(accessToken: string, user: SessionUser) {
      setToken(accessToken)
      this.user = user
    },

    // kioskTarget: the raw zbx_host/host_id from a Zabbix terminal deep-link that
    // forced this login. It's only a hint — identity-service independently resolves
    // and validates it server-side before binding anything into the issued token.
    // Returns { mfaRequired, mfaMethod } instead of resolving straight to a full
    // session when the account has MFA enabled — the token is already applied
    // (it's valid for verifyMfaLogin/resendMfaCode) but nav is deliberately NOT
    // loaded yet, since api-gateway blocks every other path until verified.
    async login(username: string, password: string, kioskTarget?: string) {
      const { data } = await api.post('/auth/login', { username, password, kiosk_target: kioskTarget })
      this._applyToken(data.access_token, data.user)
      const gate = {
        mfaRequired: !!data.mfa_required, mfaMethod: data.mfa_method as string,
        mfaSetupRequired: !!data.mfa_setup_required, needsSetupWizard: !!data.needs_setup_wizard,
      }
      if (!gate.mfaRequired && !gate.mfaSetupRequired) await this.loadNav()
      return gate
    },

    // Forced first-login rotation: the gateway 403s everything except this
    // call while the session carries the must-change-password claim. Must also
    // carry kioskTarget through — this mints a fresh token too, and a kiosk login
    // that happens to need a password change would otherwise lose the binding.
    async changePassword(currentPassword: string, newPassword: string, kioskTarget?: string) {
      const { data } = await api.post('/auth/change-password', {
        current_password: currentPassword,
        new_password: newPassword,
        kiosk_target: kioskTarget,
      })
      this._applyToken(data.access_token, data.user)
      const gate = {
        mfaRequired: !!data.mfa_required, mfaMethod: data.mfa_method as string,
        mfaSetupRequired: !!data.mfa_setup_required, needsSetupWizard: !!data.needs_setup_wizard,
      }
      if (!gate.mfaRequired && !gate.mfaSetupRequired) await this.loadNav()
      return gate
    },

    // Completes the login-time MFA gate — mints a clean, fully-usable session.
    // Still may need the setup wizard next (independent of the MFA gate).
    async verifyMfaLogin(code: string) {
      const { data } = await api.post('/auth/mfa/verify-login', { code })
      this._applyToken(data.access_token, data.user)
      await this.loadNav()
      return { needsSetupWizard: !!data.needs_setup_wizard }
    },

    async resendMfaCode() {
      await api.post('/auth/mfa/resend')
    },

    // /auth/mfa/enable now always re-mints a clean session (see auth.py) — matters
    // for the group-enforced enrollment path, harmless for voluntary self-service
    // enrollment. Callers (SecurityView.vue, LoginView.vue) apply the returned
    // token via this, and check the wizard flag (relevant when enrollment was
    // itself the last gate — the forced-enrollment path in LoginView.vue).
    async applyMfaEnableResult(accessToken: string, user: SessionUser, needsSetupWizard: boolean) {
      this._applyToken(accessToken, user)
      await this.loadNav()
      return { needsSetupWizard: !!needsSetupWizard }
    },

    async completeSetupWizard() {
      await api.post('/auth/setup/complete')
    },

    // Zabbix SSO is a parallel credential path, not exempt from a user's own MFA
    // setting — same mfa_required shape as login()/changePassword().
    async exchangeSSO(ssoCode: string) {
      const { data } = await api.post('/auth/sso-exchange', { sso_code: ssoCode })
      this._applyToken(data.access_token, data.user)
      const gate = {
        mfaRequired: !!data.mfa_required, mfaMethod: data.mfa_method as string,
        mfaSetupRequired: !!data.mfa_setup_required, needsSetupWizard: !!data.needs_setup_wizard,
      }
      if (!gate.mfaRequired && !gate.mfaSetupRequired) await this.loadNav()
      return gate
    },

    // Instant server-side revocation, not just forgetting the token
    // client-side — the session is DEL'd in Redis, dead the moment this
    // returns (see api-gateway POST /auth/logout).
    async logout() {
      try { await api.post('/auth/logout') } catch { /* best-effort — clear local state regardless */ }
      clearToken()
      this.user = null
      this.nav = {}
      this.navReady = false
    },
  },
})
