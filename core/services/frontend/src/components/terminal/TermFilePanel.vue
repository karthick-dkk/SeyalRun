<template>
  <aside class="fm" @click.stop>
    <header class="fm-head">
      <svg class="icon-inline" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l1.5 2h9.5A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z"/></svg>
      <span class="fm-title">Files</span>
      <span class="fm-host">{{ hostLabel }}</span>
      <button class="fm-x" @click="$emit('close')" title="Close file manager">✕</button>
    </header>

    <div class="fm-path">
      <button class="fm-up" :disabled="cwd === '/' || loading" @click="up" title="Parent directory">↑</button>
      <input v-model="pathInput" class="fm-path-input" spellcheck="false" @keyup.enter="go(pathInput)" />
      <button class="fm-btn" :disabled="loading" @click="go(pathInput)" title="Go">Go</button>
    </div>

    <div class="fm-actions">
      <button class="fm-btn" :disabled="loading" @click="refresh">Refresh</button>
      <button class="fm-btn" :disabled="loading" @click="promptMkdir">New Folder</button>
      <label class="fm-btn fm-upload" :class="{ disabled: loading }" :title="`Upload a file from this computer into ${DEFAULT_PATH}`">
        Upload
        <input type="file" hidden @change="onUpload" />
      </label>
    </div>

    <p v-if="error" class="fm-error">{{ error }}</p>

    <ul class="fm-list">
      <li v-if="loading" class="fm-muted">Loading…</li>
      <li v-else-if="!entries.length" class="fm-muted">Empty directory</li>
      <li
        v-for="e in entries"
        :key="e.path"
        class="fm-row"
        :class="{ 'is-dir': e.is_dir }"
        @dblclick="e.is_dir ? go(e.path) : download(e)"
      >
        <span class="fm-ico">{{ e.is_dir ? '📁' : '📄' }}</span>
        <span class="fm-name" :title="e.path">{{ e.name }}</span>
        <span class="fm-size">{{ e.is_dir ? '—' : humanSize(e.size) }}</span>
        <span class="fm-row-actions">
          <button v-if="!e.is_dir" class="fm-mini" title="Download" @click.stop="download(e)">⭳</button>
          <button class="fm-mini" title="Rename" @click.stop="promptRename(e)">✎</button>
          <button class="fm-mini fm-danger" title="Delete" @click.stop="remove(e)">🗑</button>
        </span>
      </li>
    </ul>
  </aside>
</template>

<script setup lang="ts">
/**
 * SFTP file manager for a live terminal session — Increment 1.
 *
 * Deliberately bound to a session rather than a host: the backend runs these
 * operations over the SSH connection that session already opened, so there is
 * one authentication and one credential unwrap, and every transfer is
 * attributable to the session it happened in.
 *
 * Authorization lives on the server, not here. This panel does not try to hide
 * Download when the caller lacks the `download` grant — a 403 is surfaced as
 * the message the API returns, so what the operator sees is what was actually
 * enforced rather than a guess this component made.
 */
import { ref, computed, onMounted } from 'vue'
import api from '@/api/client'

const props = defineProps<{ sessionId: string; hostLabel?: string }>()
defineEmits<{ (e: 'close'): void }>()

// Opens at /tmp on every host — it exists everywhere, every account can read
// it, and it is the conventional drop point for these transfers. The server
// applies the same default, so the two cannot disagree.
const DEFAULT_PATH = '/tmp'
const cwd = ref(DEFAULT_PATH)
const pathInput = ref(DEFAULT_PATH)
const entries = ref<any[]>([])
const loading = ref(false)
const error = ref('')

function humanSize(n: number): string {
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = n / 1024, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`
}

/** Surface what the server actually said — a 403 here is the authorization
 *  decision, and paraphrasing it would hide which grant is missing. */
function fail(e: any, fallback: string) {
  error.value = e?.response?.data?.detail || fallback
}

async function go(path: string) {
  loading.value = true
  error.value = ''
  try {
    const { data } = await api.get(`/sftp/${props.sessionId}/list`, { params: { path } })
    cwd.value = data.path
    pathInput.value = data.path
    entries.value = data.entries
  } catch (e: any) {
    fail(e, 'Could not list this directory')
  } finally {
    loading.value = false
  }
}

const refresh = () => go(cwd.value)
const up = () => go(cwd.value.replace(/\/[^/]+\/?$/, '') || '/')

async function download(e: any) {
  error.value = ''
  try {
    // Fetched as a blob so an authorization failure surfaces as a message here
    // rather than as a browser navigation to an error page.
    const resp = await api.get(`/sftp/${props.sessionId}/download`, {
      params: { path: e.path }, responseType: 'blob',
    })
    const url = URL.createObjectURL(resp.data)
    const a = document.createElement('a')
    a.href = url; a.download = e.name
    document.body.appendChild(a); a.click(); a.remove()
    URL.revokeObjectURL(url)
  } catch (err: any) {
    // A blob-typed error body has to be read back as text before the server's
    // detail is legible.
    if (err?.response?.data instanceof Blob) {
      try {
        const parsed = JSON.parse(await err.response.data.text())
        error.value = parsed.detail || 'Download refused'
        return
      } catch { /* fall through */ }
    }
    fail(err, 'Download refused')
  }
}

async function onUpload(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  error.value = ''
  loading.value = true
  try {
    const fd = new FormData()
    // Always the root, never the directory currently being browsed: the server
    // pins writes to the drop point and would refuse anything else, so sending
    // cwd would just produce a confusing 403 when the user has navigated away.
    fd.append('path', DEFAULT_PATH)
    fd.append('file', file)
    await api.post(`/sftp/${props.sessionId}/upload`, fd)
    await refresh()
  } catch (e: any) {
    fail(e, 'Upload refused')
  } finally {
    loading.value = false
    input.value = ''
  }
}

async function promptMkdir() {
  const name = window.prompt('New folder name:')
  if (!name) return
  error.value = ''
  try {
    await api.post(`/sftp/${props.sessionId}/mkdir`, { path: `${cwd.value}/${name}` })
    await refresh()
  } catch (e: any) { fail(e, 'Could not create folder') }
}

async function promptRename(e: any) {
  const name = window.prompt('Rename to:', e.name)
  if (!name || name === e.name) return
  error.value = ''
  try {
    await api.post(`/sftp/${props.sessionId}/rename`, { path: e.path, new_path: `${cwd.value}/${name}` })
    await refresh()
  } catch (err: any) { fail(err, 'Rename failed') }
}

async function remove(e: any) {
  if (!window.confirm(`Delete ${e.is_dir ? 'folder' : 'file'} "${e.name}"?`)) return
  error.value = ''
  try {
    await api.delete(`/sftp/${props.sessionId}/rm`, { params: { path: e.path, is_dir: e.is_dir } })
    await refresh()
  } catch (err: any) { fail(err, 'Delete failed') }
}

onMounted(() => go(DEFAULT_PATH))
</script>

<style scoped>
.fm {
  display: flex; flex-direction: column;
  width: 340px; min-width: 340px;
  background: #0d1117; border-left: 1px solid #21262d; color: #c9d1d9;
  font-size: 12px; overflow: hidden;
}
.fm-head { display: flex; align-items: center; gap: 6px; padding: 8px 10px; border-bottom: 1px solid #21262d; }
.fm-title { font-weight: 600; color: #e6edf3; }
.fm-host { margin-left: auto; color: #8b949e; font-size: 11px; max-width: 130px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fm-x { background: none; border: 0; color: #8b949e; cursor: pointer; padding: 2px 4px; }
.fm-x:hover { color: #f85149; }
.fm-path { display: flex; gap: 4px; padding: 6px 8px; border-bottom: 1px solid #21262d; }
.fm-path-input { flex: 1; min-width: 0; background: #161b22; border: 1px solid #30363d; color: #c9d1d9; border-radius: 4px; padding: 3px 6px; font-family: ui-monospace, monospace; font-size: 11px; }
.fm-up, .fm-btn { background: #21262d; border: 1px solid #30363d; color: #c9d1d9; border-radius: 4px; padding: 3px 8px; cursor: pointer; font-size: 11px; }
.fm-up:hover:not(:disabled), .fm-btn:hover:not(.disabled) { background: #30363d; }
.fm-up:disabled, .fm-btn.disabled { opacity: .5; cursor: default; }
.fm-actions { display: flex; gap: 4px; padding: 6px 8px; border-bottom: 1px solid #21262d; }
.fm-upload { display: inline-flex; align-items: center; }
.fm-error { margin: 8px; padding: 6px 8px; background: #3d1418; border: 1px solid #6e2b31; border-radius: 4px; color: #ffa198; font-size: 11px; }
.fm-list { flex: 1; overflow-y: auto; list-style: none; margin: 0; padding: 4px 0; }
.fm-muted { padding: 10px; color: #8b949e; }
.fm-row { display: flex; align-items: center; gap: 6px; padding: 4px 8px; cursor: default; }
.fm-row:hover { background: #161b22; }
.fm-row.is-dir { cursor: pointer; }
.fm-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fm-size { color: #8b949e; font-size: 11px; }
.fm-row-actions { display: flex; gap: 2px; opacity: 0; }
.fm-row:hover .fm-row-actions { opacity: 1; }
.fm-mini { background: none; border: 0; color: #8b949e; cursor: pointer; padding: 1px 3px; border-radius: 3px; }
.fm-mini:hover { background: #30363d; color: #e6edf3; }
.fm-danger:hover { color: #f85149; }
.icon-inline { width: 14px; height: 14px; }
@media (prefers-reduced-motion: reduce) { .fm-row-actions { opacity: 1; } }
</style>
