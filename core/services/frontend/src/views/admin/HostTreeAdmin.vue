<template>
  <div class="ht">
    <div class="card ht-tree">
      <div class="card-header">
        Host tree
        <button class="btn btn-primary btn-sm" type="button" @click="openCreate(null)">+ Group</button>
      </div>
      <p class="ht-help text-muted-sm">
        Host groups nested as a tree. An access rule on a group also covers every group below it.
        Viewers of a node can <b>see</b> its hosts — they cannot log in, transfer files or reveal credentials.
      </p>
      <div v-if="error" class="ht-error" role="alert">{{ error }}</div>
      <div v-if="loading && !groups.length" class="empty-cell">Loading…</div>
      <div v-else-if="!groups.length" class="empty-cell">No host groups yet.</div>
      <ul v-else class="ht-list" role="tree">
        <li v-for="row in visibleRows" :key="row.g.id" role="treeitem" :aria-expanded="row.hasKids ? !collapsed.has(row.g.id) : undefined"
          :aria-selected="selectedId === row.g.id">
          <div class="ht-row" :class="{ on: selectedId === row.g.id }" :style="{ paddingLeft: 10 + row.depth * 18 + 'px' }" @click="selectedId = row.g.id">
            <button v-if="row.hasKids" class="ht-twisty" type="button" :aria-label="collapsed.has(row.g.id) ? 'Expand' : 'Collapse'"
              @click.stop="toggle(row.g.id)">{{ collapsed.has(row.g.id) ? '▸' : '▾' }}</button>
            <span v-else class="ht-twisty ht-leaf">·</span>
            <span class="ht-name">{{ row.g.name }}</span>
            <span class="text-muted-sm">{{ directHosts(row.g.id) }} host{{ directHosts(row.g.id) === 1 ? '' : 's' }}</span>
            <span v-if="viewersOf(row.g.id).length" class="badge badge-blue" :title="`${viewersOf(row.g.id).length} viewer(s)`">👁 {{ viewersOf(row.g.id).length }}</span>
          </div>
        </li>
      </ul>
    </div>

    <div v-if="selected" class="card ht-detail">
      <div class="card-header">
        {{ selected.name }}
        <div class="form-actions">
          <button class="btn btn-sm" type="button" @click="openCreate(selected.id)">+ Child group</button>
          <button class="btn btn-sm btn-danger-outline" type="button" :disabled="busy" @click="removeGroup(selected)">Delete</button>
        </div>
      </div>
      <div class="card-body">
        <div class="form-group">
          <label class="form-label" for="ht-parent">Parent</label>
          <div class="ht-inline">
            <select id="ht-parent" v-model="parentDraft" class="input">
              <option value="">— none (top level) —</option>
              <option v-for="p in parentChoices" :key="p.id" :value="p.id">{{ p.label }}</option>
            </select>
            <button class="btn btn-primary btn-sm" type="button" :disabled="busy || parentDraft === (selected.parent_id || '')" @click="saveParent">Move</button>
          </div>
        </div>
        <p class="text-muted-sm">
          {{ directHosts(selected.id) }} host(s) in this group · {{ subtreeHosts(selected.id) }} host(s) including groups below it.
        </p>

        <div class="ht-section">Viewers <span class="text-muted-sm">(see hosts only)</span></div>
        <table v-if="viewersOf(selected.id).length" class="table">
          <tbody>
            <tr v-for="v in viewersOf(selected.id)" :key="v.id">
              <td>
                <span class="badge" :class="v.user_id ? 'badge-gray' : 'badge-blue'">{{ v.user_id ? 'User' : 'Group' }}</span>
                {{ v.user_id ? userName(v.user_id) : groupName(v.user_group_id) }}
              </td>
              <td class="ht-right"><button class="btn btn-sm" type="button" :disabled="busy" @click="removeViewer(v)">Remove</button></td>
            </tr>
          </tbody>
        </table>
        <div v-else class="text-muted-sm ht-none">Nobody views this node directly.</div>
        <p v-if="inheritedViewers.length" class="text-muted-sm">
          Also visible to viewers of a parent: {{ inheritedViewers.join(', ') }}
        </p>

        <div class="ht-inline ht-add">
          <select v-model="addKind" class="input ht-kind" aria-label="Viewer type">
            <option value="user">User</option>
            <option value="group">User group</option>
          </select>
          <select v-model="addId" class="input" aria-label="Viewer">
            <option value="">Choose…</option>
            <option v-for="o in addChoices" :key="o.id" :value="o.id">{{ o.label }}</option>
          </select>
          <button class="btn btn-primary btn-sm" type="button" :disabled="busy || !addId" @click="addViewer">Add viewer</button>
        </div>
      </div>
    </div>
    <div v-else-if="groups.length" class="card ht-detail empty-cell">Select a group to manage it.</div>

    <div v-if="createOpen" class="modal-overlay" @click.self="createOpen = false">
      <form class="modal" @submit.prevent="createGroup" @click.stop>
        <div class="modal-header">New host group</div>
        <div class="modal-body">
          <div class="form-group">
            <label class="form-label" for="ht-new-name">Name</label>
            <input id="ht-new-name" v-model="createForm.name" class="input" required maxlength="200" />
          </div>
          <div class="form-group">
            <label class="form-label" for="ht-new-parent">Parent</label>
            <select id="ht-new-parent" v-model="createForm.parent_id" class="input">
              <option value="">— none (top level) —</option>
              <option v-for="p in allChoices" :key="p.id" :value="p.id">{{ p.label }}</option>
            </select>
          </div>
          <div v-if="createError" class="ht-error">{{ createError }}</div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn" @click="createOpen = false">Cancel</button>
          <button type="submit" class="btn btn-primary" :disabled="busy || !createForm.name.trim()">Create</button>
        </div>
      </form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import api from '@/api/client'

interface Group { id: string; name: string; description?: string; parent_id: string | null }
interface Viewer { id: string; host_group_id: string; user_id: string | null; user_group_id: string | null }

const groups = ref<Group[]>([])
const viewers = ref<Viewer[]>([])
const hosts = ref<any[]>([])
const users = ref<any[]>([])
const userGroups = ref<any[]>([])
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const selectedId = ref<string | null>(null)
const collapsed = reactive(new Set<string>())
const parentDraft = ref('')
const addKind = ref<'user' | 'group'>('user')
const addId = ref('')
const createOpen = ref(false)
const createError = ref('')
const createForm = reactive({ name: '', parent_id: '' })

const byId = computed(() => new Map(groups.value.map(g => [g.id, g])))
const kids = computed(() => {
  const m = new Map<string, Group[]>()
  for (const g of groups.value) {
    // A parent that no longer exists is shown at the top level rather than lost.
    const p = g.parent_id && byId.value.has(g.parent_id) ? g.parent_id : ''
    if (!m.has(p)) m.set(p, [])
    m.get(p)!.push(g)
  }
  for (const list of m.values()) list.sort((a, b) => a.name.localeCompare(b.name))
  return m
})

function descendantsOf(id: string): Set<string> {
  const out = new Set<string>()
  const stack = [id]
  while (stack.length) {
    const cur = stack.pop()!
    if (out.has(cur)) continue
    out.add(cur)
    for (const k of kids.value.get(cur) || []) stack.push(k.id)
  }
  return out
}

/** Depth-first rows; a collapsed node hides its subtree. Cycle-safe. */
const visibleRows = computed(() => {
  const rows: { g: Group; depth: number; hasKids: boolean }[] = []
  const seen = new Set<string>()
  const walk = (parent: string, depth: number) => {
    for (const g of kids.value.get(parent) || []) {
      if (seen.has(g.id)) continue
      seen.add(g.id)
      const hasKids = (kids.value.get(g.id) || []).length > 0
      rows.push({ g, depth, hasKids })
      if (hasKids && !collapsed.has(g.id)) walk(g.id, depth + 1)
    }
  }
  walk('', 0)
  return rows
})

function pathLabel(g: Group): string {
  const parts: string[] = []
  const seen = new Set<string>()
  let cur: Group | undefined = g
  while (cur && !seen.has(cur.id)) {
    seen.add(cur.id)
    parts.unshift(cur.name)
    cur = cur.parent_id ? byId.value.get(cur.parent_id) : undefined
  }
  return parts.join(' / ')
}
const allChoices = computed(() => groups.value.map(g => ({ id: g.id, label: pathLabel(g) })).sort((a, b) => a.label.localeCompare(b.label)))

const selected = computed(() => (selectedId.value ? byId.value.get(selectedId.value) ?? null : null))
// A group cannot move under itself or anything below it — the server refuses it too.
const parentChoices = computed(() => {
  if (!selected.value) return []
  const blocked = descendantsOf(selected.value.id)
  return allChoices.value.filter(c => !blocked.has(c.id))
})
watch(selected, (g) => { parentDraft.value = g?.parent_id || ''; addId.value = '' })

const directHosts = (gid: string) => hosts.value.filter(h => (h.group_ids || []).includes(gid)).length
function subtreeHosts(gid: string): number {
  const all = descendantsOf(gid)
  return hosts.value.filter(h => (h.group_ids || []).some((x: string) => all.has(x))).length
}
const viewersOf = (gid: string) => viewers.value.filter(v => v.host_group_id === gid)
const userName = (id: string | null) => users.value.find(u => u.id === id)?.username || id || '—'
const groupName = (id: string | null) => userGroups.value.find(g => g.id === id)?.name || id || '—'

const inheritedViewers = computed(() => {
  if (!selected.value) return []
  const out: string[] = []
  const seen = new Set<string>([selected.value.id])
  let cur = selected.value.parent_id ? byId.value.get(selected.value.parent_id) : undefined
  while (cur && !seen.has(cur.id)) {
    seen.add(cur.id)
    for (const v of viewersOf(cur.id)) out.push(`${v.user_id ? userName(v.user_id) : groupName(v.user_group_id)} (${cur.name})`)
    cur = cur.parent_id ? byId.value.get(cur.parent_id) : undefined
  }
  return out
})

const addChoices = computed(() => {
  const gid = selectedId.value
  const existing = new Set(viewersOf(gid || '').map(v => (addKind.value === 'user' ? v.user_id : v.user_group_id)))
  const list = addKind.value === 'user'
    ? users.value.map(u => ({ id: u.id, label: u.display_name ? `${u.username} — ${u.display_name}` : u.username }))
    : userGroups.value.map(g => ({ id: g.id, label: g.name }))
  return list.filter(o => !existing.has(o.id)).sort((a, b) => a.label.localeCompare(b.label))
})
watch(addKind, () => { addId.value = '' })

function toggle(id: string) { collapsed.has(id) ? collapsed.delete(id) : collapsed.add(id) }
const detail = (e: any, fallback: string) => {
  const d = e?.response?.data?.detail
  return typeof d === 'string' ? d : fallback
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [g, v, h, u, ug] = await Promise.all([
      api.get('/host-groups'),
      api.get('/authorizations/host-group-viewers'),
      api.get('/hosts').catch(() => ({ data: [] })),
      api.get('/users').catch(() => ({ data: [] })),
      api.get('/users/groups').catch(() => ({ data: [] })),
    ])
    groups.value = g.data || []
    viewers.value = v.data || []
    hosts.value = h.data || []
    users.value = u.data || []
    userGroups.value = ug.data || []
    if (selectedId.value && !byId.value.has(selectedId.value)) selectedId.value = null
  } catch (e: any) {
    error.value = detail(e, 'Could not load the host tree.')
  } finally {
    loading.value = false
  }
}

async function run(fn: () => Promise<unknown>, fallback: string) {
  busy.value = true
  error.value = ''
  try { await fn(); await load() } catch (e: any) { error.value = detail(e, fallback) } finally { busy.value = false }
}

const saveParent = () => selected.value && run(
  () => api.put(`/host-groups/${selected.value!.id}`, { parent_id: parentDraft.value || null }), 'Move failed.')

function removeGroup(g: Group) {
  if (!confirm(`Delete host group "${g.name}"? Hosts stay, but lose this group.`)) return
  run(async () => { await api.delete(`/host-groups/${g.id}`); selectedId.value = null }, 'Delete failed.')
}

const addViewer = () => selected.value && addId.value && run(() => api.post('/authorizations/host-group-viewers', {
  host_group_id: selected.value!.id,
  user_ids: addKind.value === 'user' ? [addId.value] : [],
  user_group_ids: addKind.value === 'group' ? [addId.value] : [],
}), 'Could not add the viewer.')

const removeViewer = (v: Viewer) => run(() => api.delete(`/authorizations/host-group-viewers/${v.id}`), 'Could not remove the viewer.')

function openCreate(parentId: string | null) {
  createForm.name = ''
  createForm.parent_id = parentId || ''
  createError.value = ''
  createOpen.value = true
}
async function createGroup() {
  busy.value = true
  createError.value = ''
  try {
    const r = await api.post('/host-groups', { name: createForm.name.trim(), parent_id: createForm.parent_id || null })
    createOpen.value = false
    if (createForm.parent_id) collapsed.delete(createForm.parent_id)
    await load()
    selectedId.value = r.data?.id ?? null
  } catch (e: any) {
    createError.value = detail(e, 'Create failed.')
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.ht { display: grid; grid-template-columns: minmax(280px, 1fr) minmax(320px, 1.2fr); gap: 16px; align-items: start; }
@media (max-width: 900px) { .ht { grid-template-columns: 1fr; } }
.ht-help { margin: 0; padding: 10px 16px; border-bottom: 1px solid var(--border); line-height: 1.5; }
.ht-error { color: var(--danger); font-size: 12.5px; padding: 8px 16px; }
.ht-list { list-style: none; margin: 0; padding: 6px 0; max-height: 70vh; overflow: auto; }
.ht-row { display: flex; align-items: center; gap: 8px; padding: 6px 12px; cursor: pointer; font-size: 13px; border-left: 2px solid transparent; }
.ht-row:hover { background: var(--bg3); }
.ht-row.on { background: var(--bg3); border-left-color: var(--accent2); }
.ht-twisty { width: 18px; flex: 0 0 18px; background: none; border: 0; color: var(--text2); cursor: pointer; padding: 0; font-size: 12px; text-align: center; }
.ht-leaf { cursor: default; }
.ht-name { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ht-inline { display: flex; gap: 8px; align-items: center; }
.ht-kind { max-width: 130px; }
.ht-add { margin-top: 12px; }
.ht-section { font-weight: 600; font-size: 13px; margin: 18px 0 8px; }
.ht-none { padding: 4px 0; }
.ht-right { text-align: right; }
</style>
