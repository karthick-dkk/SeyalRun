<template>
  <div v-if="visible" class="cp-overlay" @click.self="$emit('close')">
    <div class="cp">
      <div class="cp-head">
        <span>Connect to <strong>{{ host?.name }}</strong></span>
        <button class="cp-close" @click="$emit('close')">✕</button>
      </div>

      <div class="cp-body">
        <!-- Deep links arrive from Zabbix, where the person at the keyboard may not
             be the person signed into SeyalRun. The session records under THIS
             identity, so say so before anything connects. -->
        <div v-if="deepLink" class="cp-deeplink">
          <div><strong>Requested from Zabbix.</strong> Nothing connects until you choose a login below.</div>
          <div class="cp-deeplink-id">
            Signed in as <strong>{{ signedInAs }}</strong> — the session is recorded under this
            identity. Not you? Log out and back in as yourself first.
          </div>
        </div>

        <p class="cp-hint">Select a login:</p>

        <div
          v-for="cred in ordered"
          :key="cred.id"
          class="cp-row"
          :class="{ 'cp-row--remembered': cred.id === rememberedId }"
        >
          <div class="cp-who">
            <span class="cp-user">{{ cred.username || cred.name }}</span>
            <span v-if="cred.is_default" class="cp-tag cp-tag--default">default</span>
            <span v-if="cred.is_sudo" class="cp-tag">sudo</span>
            <span v-if="cred.id === rememberedId" class="cp-tag cp-tag--saved">saved</span>
          </div>
          <div class="cp-go">
            <button class="cp-btn cp-btn--ssh" @click="pick(cred, 'ssh')">▶ SSH</button>
            <button class="cp-btn" @click="pick(cred, 'sftp')" title="Open the file browser on this host">⁂ SFTP</button>
          </div>
        </div>

        <!-- Manual login. Only offered when the authorization for this host grants
             it: bringing your own account bypasses the vault entirely — no
             rotation, no record of WHICH stored credential was used — so it is a
             permission, not a convenience. -->
        <template v-if="manualAllowed">
          <button v-if="!manual.open" class="cp-manual-toggle" @click="manual.open = true">
            + Use a different account…
          </button>
          <div v-else class="cp-manual">
            <div class="cp-field">
              <label>Username</label>
              <input v-model="manual.username" class="cp-input" spellcheck="false" autocomplete="off" />
            </div>
            <div class="cp-field">
              <label>Password</label>
              <input v-model="manual.password" type="password" class="cp-input" autocomplete="new-password" />
            </div>
            <p class="cp-manual-note">
              This account is saved to <strong>{{ host?.name }}</strong>'s credentials — encrypted in
              the vault, reusable next time, and rotatable like any other. It is not kept in this
              browser. Remove it from Admin → Credentials when it is no longer needed.
            </p>
            <div class="cp-manual-actions">
              <button class="cp-btn" @click="manual.open = false">Cancel</button>
              <button
                class="cp-btn cp-btn--ssh"
                :disabled="!manual.username.trim() || !manual.password || busy"
                @click="submitManual('ssh')"
              >{{ busy ? 'Connecting…' : '▶ SSH' }}</button>
              <button
                class="cp-btn"
                :disabled="!manual.username.trim() || !manual.password || busy"
                @click="submitManual('sftp')"
              >⁂ SFTP</button>
            </div>
          </div>
        </template>

        <p v-if="error" class="cp-error">{{ error }}</p>
      </div>

      <div class="cp-foot">
        <label class="cp-check">
          <input type="checkbox" v-model="remember" />
          <span>Remember this login for <strong>{{ host?.name }}</strong> and connect automatically</span>
        </label>
        <button v-if="rememberedId" class="cp-forget" @click="forget">Forget saved login</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * "Connect as" picker — every login available on a host, in one place.
 *
 * What is deliberately NOT here: the secret of a stored credential. Those live in
 * the vault and are unwrapped server-side at connect time; the browser never sees
 * them, and "remember" persists only which credential was chosen (an id the user
 * is already authorized for), never a password. A PAM that cached secrets in
 * localStorage would have given up the thing it exists to provide.
 */
import { computed, reactive, ref, watch } from 'vue'
import api from '@/api/client'

export type ConnectMode = 'ssh' | 'sftp'

const props = defineProps<{
  visible: boolean
  host: any
  credentials: any[]
  /** Whether this host's authorization grants ad-hoc accounts. */
  manualAllowed: boolean
  /** Opened from a Zabbix deep link rather than a click in this app. */
  deepLink?: boolean
  signedInAs?: string
}>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'pick', payload: { credentialId: string; mode: ConnectMode }): void
}>()

const remember = ref(false)
const busy = ref(false)
const error = ref('')
const manual = reactive({ open: false, username: '', password: '' })

const STORE_KEY = 'seyalrun.login.default'

/** Saved choices are per host. Storage can be unavailable (Safari blocks it in a
 *  third-party iframe, which is how this app runs inside Zabbix), so every access
 *  degrades to "nothing saved" rather than throwing. */
function readStore(): Record<string, string> {
  try { return JSON.parse(localStorage.getItem(STORE_KEY) || '{}') } catch { return {} }
}
function writeStore(map: Record<string, string>) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(map)) } catch { /* storage blocked */ }
}

const rememberedId = ref<string>('')
watch(() => [props.visible, props.host?.id], () => {
  if (!props.visible) return
  error.value = ''
  Object.assign(manual, { open: false, username: '', password: '' })
  rememberedId.value = props.host ? (readStore()[props.host.id] || '') : ''
  remember.value = !!rememberedId.value
}, { immediate: true })

/** Default first, then remembered, then the rest — the two a user reaches for. */
const ordered = computed(() => {
  const list = [...(props.credentials || [])]
  return list.sort((a, b) => {
    const score = (c: any) => (c.id === rememberedId.value ? 2 : 0) + (c.is_default ? 1 : 0)
    return score(b) - score(a)
  })
})

function persist(credentialId: string) {
  const map = readStore()
  if (!props.host) return
  if (remember.value) map[props.host.id] = credentialId
  else delete map[props.host.id]
  writeStore(map)
}

function forget() {
  const map = readStore()
  if (props.host) delete map[props.host.id]
  writeStore(map)
  rememberedId.value = ''
  remember.value = false
}

function pick(cred: any, mode: ConnectMode) {
  persist(cred.id)
  emit('pick', { credentialId: cred.id, mode })
}

/** Ad-hoc account: create it in the vault first, then connect with it like any
 *  other. Going through the same create path means it is encrypted, auditable and
 *  rotatable from the moment it exists — and there is no second code path on the
 *  server that accepts a raw secret at connect time. */
async function submitManual(mode: ConnectMode) {
  error.value = ''
  busy.value = true
  try {
    // Always stored. An earlier draft passed `ephemeral: !save` to offer a
    // "don't keep it" option — but no such field exists server-side, pydantic
    // drops unknown keys silently, and the credential would have been created
    // and kept regardless. A checkbox that says the secret is discarded while
    // the vault keeps it is worse than not offering the choice.
    const { data } = await api.post('/credentials', {
      name: manual.username.trim(),
      username: manual.username.trim(),
      secret_type: 'password',
      secret: { password: manual.password },
      host_ids: [props.host.id],
    })
    manual.password = ''
    persist(data.id)
    emit('pick', { credentialId: data.id, mode })
  } catch (e: any) {
    error.value = e?.response?.data?.detail || 'Could not use that account'
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.cp-overlay {
  position: fixed; inset: 0; z-index: 540; display: flex;
  align-items: center; justify-content: center; background: rgba(0, 0, 0, .55);
}
.cp {
  width: 460px; max-width: 94vw; background: #0d1117; color: #c9d1d9;
  border: 1px solid #30363d; border-radius: 10px; overflow: hidden;
}
.cp-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 14px; border-bottom: 1px solid #21262d; color: #58a6ff; font-weight: 600;
}
.cp-close { background: none; border: 0; color: #8b949e; cursor: pointer; font-size: 15px; }
.cp-close:hover { color: #f85149; }
.cp-body { padding: 10px 14px; max-height: 60vh; overflow-y: auto; }
.cp-hint { margin: 2px 0 10px; color: #8b949e; font-size: 13px; }
.cp-row {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  padding: 8px; border-radius: 6px; border: 1px solid transparent;
}
.cp-row:hover { background: #161b22; }
.cp-row--remembered { border-color: #1f6feb; background: rgba(31, 111, 235, .08); }
.cp-who { display: flex; align-items: center; gap: 6px; min-width: 0; }
.cp-user { font-weight: 600; color: #e6edf3; overflow: hidden; text-overflow: ellipsis; }
.cp-tag {
  font-size: 10px; text-transform: uppercase; letter-spacing: .04em;
  padding: 1px 6px; border-radius: 8px; background: #21262d; color: #8b949e;
}
.cp-tag--default { background: #1f6feb; color: #fff; }
.cp-tag--saved { background: #238636; color: #fff; }
.cp-go { display: flex; gap: 6px; flex: 0 0 auto; }
.cp-btn {
  background: #21262d; border: 1px solid #30363d; color: #c9d1d9;
  border-radius: 5px; padding: 4px 10px; cursor: pointer; font-size: 12px;
}
.cp-btn:hover:not(:disabled) { background: #30363d; }
.cp-btn:disabled { opacity: .5; cursor: default; }
.cp-btn--ssh { border-color: #1f6feb; color: #58a6ff; }
.cp-manual-toggle {
  margin-top: 8px; background: none; border: 1px dashed #30363d; color: #8b949e;
  width: 100%; padding: 7px; border-radius: 6px; cursor: pointer; font-size: 12px;
}
.cp-manual-toggle:hover { color: #c9d1d9; border-color: #484f58; }
.cp-manual { margin-top: 10px; padding: 10px; border: 1px solid #30363d; border-radius: 6px; }
.cp-field { margin-bottom: 8px; }
.cp-field label { display: block; font-size: 11px; color: #8b949e; margin-bottom: 3px; }
.cp-input {
  width: 100%; background: #0d1117; border: 1px solid #30363d; color: #c9d1d9;
  border-radius: 5px; padding: 5px 8px; font-size: 13px;
}
.cp-manual-actions { display: flex; justify-content: flex-end; gap: 6px; margin-top: 10px; }
.cp-check { display: flex; align-items: flex-start; gap: 7px; font-size: 12px; cursor: pointer; }
.cp-check em { display: block; color: #8b949e; font-style: normal; font-size: 11px; margin-top: 2px; }
.cp-error {
  margin: 10px 0 0; padding: 7px 9px; border-radius: 5px; font-size: 12px;
  background: #3d1418; border: 1px solid #6e2b31; color: #ffa198;
}
.cp-foot {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  padding: 10px 14px; border-top: 1px solid #21262d; background: #0b0f14;
}
.cp-forget { background: none; border: 0; color: #8b949e; cursor: pointer; font-size: 11px; text-decoration: underline; }
.cp-forget:hover { color: #f85149; }
.cp-manual-note {
  margin: 2px 0 0; font-size: 11px; line-height: 1.5; color: #8b949e;
}
.cp-deeplink {
  margin: 0 0 12px; padding: 9px 11px; border-radius: 6px; font-size: 12px; line-height: 1.5;
  background: rgba(31, 111, 235, .10); border: 1px solid rgba(31, 111, 235, .35);
}
.cp-deeplink-id { margin-top: 5px; color: #8b949e; }
</style>
