<template>
  <AppShell>
    <div class="page">

      <!-- ── Page header ──────────────────────────────────────────────────── -->
      <div class="page-header" style="align-items:flex-start">
        <div>
          <div class="page-title">Assets</div>
          <div class="page-subtitle">{{ filtered.length }} of {{ hosts.length }} host{{ hosts.length === 1 ? '' : 's' }}</div>
        </div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <button v-if="auth.isAdminOrSupport && selectedHostIds.size" class="btn" style="color:var(--danger);border-color:var(--danger)" @click="bulkDeleteHosts">
            ✕ Delete {{ selectedHostIds.size }} selected
          </button>
          <button v-if="auth.isAdminOrSupport" class="btn btn-primary" @click="openCreateAsset">+ Asset</button>
          <button v-if="auth.isAdminOrSupport" class="btn" :disabled="syncPending" @click="syncFromZabbix" title="Sync hosts from Zabbix" style="display:inline-flex;align-items:center;gap:5px;font-size:12px">
            <svg style="width:13px;height:13px;display:block;flex-shrink:0" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"/></svg>
            {{ syncPending ? 'Syncing…' : 'Zabbix Sync' }}
          </button>
          <span v-if="lastSynced" style="font-size:11px;color:var(--text2);white-space:nowrap">synced {{ fmtSyncTime(lastSynced) }}</span>
          <button class="btn" style="font-size:12px" @click="exportHostsCsv" title="Export all hosts to CSV (no user or credential data included)">⇩ Export CSV</button>
          <button v-if="auth.isAdminOrSupport" class="btn" style="font-size:12px" :disabled="csvImporting" @click="csvFileInput?.click()" title="Import hosts from CSV — never deletes existing hosts">
            {{ csvImporting ? 'Importing…' : '⇧ Import CSV' }}
          </button>
          <input ref="csvFileInput" type="file" accept=".csv,text/csv" style="display:none" @change="handleCsvFile" />
          <button class="btn btn-icon" @click="load" title="Refresh"><svg style="width:14px;height:14px;display:block" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"/></svg></button>
        </div>
      </div>
      <div v-if="csvImportError" style="color:var(--danger);font-size:13px;padding:10px 14px;background:rgba(248,81,73,0.08);border-radius:6px;border:1px solid rgba(248,81,73,0.3);margin-bottom:12px">{{ csvImportError }}</div>

      <!-- ── Toolbar ──────────────────────────────────────────────────────── -->
      <div class="assets-toolbar">
        <input v-model="search" class="input" placeholder="Search host, IP, or group…" style="max-width:240px;font-size:13px" />
        <select v-model="osFilter" class="input" style="max-width:128px;font-size:13px">
          <option value="">All platforms</option>
          <option value="linux">Linux</option>
          <option value="windows">Windows</option>
        </select>
        <select v-model="sourceFilter" class="input" style="max-width:128px;font-size:13px">
          <option value="">All sources</option>
          <option value="seyalrun">SeyalRun</option>
          <option value="zabbix">Zabbix</option>
        </select>
      </div>

      <!-- ── Hosts table ──────────────────────────────────────────────────── -->
      <div class="card" style="margin-top:0">
        <div v-if="loading" style="padding:32px;text-align:center;color:var(--text2)">Loading…</div>
        <table v-else class="table">
          <thead>
            <tr>
              <th v-if="auth.isAdminOrSupport" style="width:32px">
                <input type="checkbox" :checked="allDeletableHostsSelected" @change="toggleSelectAllHosts" />
              </th>
              <th style="width:44px"></th>
              <th class="th-sort" @click="toggleSort('name')">Name <span class="sort-arrow" v-if="sortKey==='name'">{{ sortDir===1?'▲':'▼' }}</span></th>
              <th class="th-sort" @click="toggleSort('ip')">Address <span class="sort-arrow" v-if="sortKey==='ip'">{{ sortDir===1?'▲':'▼' }}</span></th>
              <th>Port</th>
              <th>Platform</th>
              <th>Zone</th>
              <th v-if="auth.isAdminOrSupport">Host Groups</th>
              <th v-if="auth.isAdminOrSupport" class="th-center">Users</th>
              <th v-if="auth.isAdminOrSupport" class="th-center">Credentials</th>
              <th>Status</th>
              <th v-if="auth.isAdminOrSupport"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="h in filtered" :key="h.id" :class="{ 'tr--inactive': !h.enabled }">
              <td v-if="auth.isAdminOrSupport">
                <input
                  v-if="!h.zabbix_hostid"
                  type="checkbox"
                  :checked="selectedHostIds.has(h.id)"
                  @change="toggleHostSelect(h.id)"
                />
              </td>
              <!-- SSH connect button — Linux only; Windows hosts use RDP, not SSH, and the
                   Zabbix module's native Hosts page applies the same gate for consistency -->
              <td style="padding:6px 8px">
                <button
                  v-if="h.os_type !== 'windows'"
                  class="ssh-icon-btn"
                  :disabled="!h.enabled"
                  :title="`SSH into ${h.name} (${h.ip})`"
                  @click="connectSSH(h)"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6.75 7.5l3 2.25-3 2.25m4.5 0h3m-9 8.25h13.5A2.25 2.25 0 0021 18V6a2.25 2.25 0 00-2.25-2.25H5.25A2.25 2.25 0 003 6v12a2.25 2.25 0 002.25 2.25z"/></svg>
                </button>
              </td>
              <td>
                <div style="display:flex;align-items:center;gap:6px;font-weight:600">
                  <a class="host-name-link" @click="openHostDetail(h)" :title="`Open ${h.name} details`">{{ h.name }}</a>
                  <span v-if="h.zabbix_hostid" class="src-badge src-badge--zbx" title="Synced from Zabbix">Z</span>
                  <span v-else class="src-badge src-badge--sr" title="SeyalRun native host">S</span>
                </div>
              </td>
              <td><span class="ip-mono">{{ h.ip }}</span></td>
              <td><span class="ip-mono" style="font-size:12px">{{ h.port || 22 }}</span></td>
              <td style="color:var(--text2)">{{ h.os_type === 'windows' ? 'Windows' : 'Linux' }}</td>
              <td>
                <span v-if="zoneName(h)" class="zone-badge"><span class="zone-badge-icon">⊕</span>{{ zoneName(h) }}</span>
                <span v-else style="color:var(--text2);font-size:12px">—</span>
              </td>
              <td v-if="auth.isAdminOrSupport">
                <span v-for="g in groupNames(h)" :key="g" class="badge badge-blue" style="margin-right:4px">{{ g }}</span>
                <span v-if="!groupNames(h).length" style="color:var(--text2);font-size:12px">—</span>
              </td>
              <td v-if="auth.isAdminOrSupport" class="td-center">
                <button class="count-link" :class="{ 'count-link--zero': !hostAuthzMap.get(h.id)?.length }" @click="openUsersDrawer(h)">
                  <span class="count-link-num">{{ hostAuthzMap.get(h.id)?.length || 0 }}</span>
                  <span class="count-link-label">{{ (hostAuthzMap.get(h.id)?.length || 0) === 1 ? 'user' : 'users' }}</span>
                </button>
              </td>
              <td v-if="auth.isAdminOrSupport" class="td-center">
                <button class="count-link" :class="{ 'count-link--zero': !hostCredMap.get(h.id)?.length }" @click="openCredsDrawer(h)">
                  <span class="count-link-num">{{ hostCredMap.get(h.id)?.length || 0 }}</span>
                  <span class="count-link-label">{{ (hostCredMap.get(h.id)?.length || 0) === 1 ? 'cred' : 'creds' }}</span>
                </button>
              </td>
              <td>
                <span v-if="h.enabled" class="status-ok">Active</span>
                <span v-else class="status-off">Inactive</span>
              </td>
              <td v-if="auth.isAdminOrSupport">
                <div style="display:flex;gap:8px;justify-content:flex-end">
                  <button class="btn-icon" title="Edit" @click="openEditAsset(h)"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor"><path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z"/></svg></button>
                  <button v-if="!h.zabbix_hostid" class="btn-icon btn-icon-danger" title="Delete" @click="deleteAsset(h)"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd"/></svg></button>
                  <span v-else class="btn-icon" style="opacity:0.35;cursor:not-allowed" title="Zabbix-synced hosts can't be deleted here — remove in Zabbix and re-sync"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd"/></svg></span>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="!filtered.length && !loading" style="padding:32px;text-align:center;color:var(--text2)">No hosts found.</div>
        <div style="padding:10px 16px;font-size:13px;color:var(--text2);font-family:var(--font-mono);border-top:1px solid var(--border)">
          {{ hosts.length }} total &nbsp;·&nbsp; {{ hosts.filter(h=>h.enabled).length }} active &nbsp;·&nbsp; {{ hosts.filter(h=>!h.enabled).length }} inactive
        </div>
      </div>

    </div><!-- /page -->

    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <!-- SIDE PANEL: Create / Edit Asset (compact, right-anchored)             -->
    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <Teleport to="body">
      <div v-if="showAssetDrawer" class="side-overlay" @click.self="closeAssetDrawer">
        <div class="side-backdrop" @click="closeAssetDrawer" />
        <div class="side-panel side-panel--wide">
          <div class="side-header">
            <span>{{ editingAsset ? `Edit — ${editingAsset.name}` : 'Add Asset' }}</span>
            <button class="btn btn-sm btn-icon" @click="closeAssetDrawer">✕</button>
          </div>
          <div class="side-body">
            <div class="edit-cols">
              <!-- LEFT: host details -->
              <div class="edit-col">
                <div class="asset-form-section-label">Host Details</div>
                <div class="form-grid-2">
                  <div class="form-group"><label class="form-label">Name</label><input v-model="assetForm.name" class="input" placeholder="e.g. db-prod-01" /></div>
                  <div class="form-group"><label class="form-label">IP Address</label><input v-model="assetForm.ip" class="input" placeholder="10.0.0.5" /></div>
                  <div class="form-group"><label class="form-label">SSH Port</label><input v-model.number="assetForm.port" type="number" min="1" max="65535" class="input" /></div>
                  <div class="form-group"><label class="form-label">Platform</label><select v-model="assetForm.os_type" class="input"><option value="linux">Linux</option><option value="windows">Windows</option></select></div>
                  <div class="form-group"><label class="form-label">Zone (ProxyJump)</label><select v-model="assetForm.zone_id" class="input"><option value="">— No Zone —</option><option v-for="z in zones" :key="z.id" :value="z.id">{{ z.name }}</option></select></div>
                  <div class="form-group"><label class="form-label">Status</label><select v-model="assetForm.enabled" class="input"><option :value="true">Active</option><option :value="false">Inactive</option></select></div>
                  <div class="form-group" style="grid-column:1 / -1">
                    <label class="form-label">Host Groups</label>
                    <AsyncPicker v-model="assetForm.groups" :search-fn="searchHostGroups" placeholder="Search host groups…" />
                    <div style="display:flex;gap:6px;margin-top:6px">
                      <input v-model="newGroupName" class="input" placeholder="New host group name…" style="font-size:12px" @keyup.enter.prevent="createGroup" />
                      <button class="btn btn-sm" :disabled="!newGroupName.trim() || creatingGroup" @click="createGroup">{{ creatingGroup ? '…' : '+ Add' }}</button>
                    </div>
                    <div v-if="groupError" class="err">{{ groupError }}</div>
                  </div>
                  <div class="form-group" style="grid-column:1 / -1"><label class="form-label">Expires</label><input v-model="assetForm.date_expired" type="date" class="input" title="Leave blank for no expiry" /></div>
                </div>
              </div>

              <!-- RIGHT: users + credentials + push -->
              <div class="edit-col">
                <!-- Allowed users -->
                <div class="section-head" style="margin-top:0">
                  <div class="asset-form-section-label" style="margin-bottom:0">Allowed Users</div>
                  <span v-if="assetForm.allowedUserIds.length" class="pill-count">{{ assetForm.allowedUserIds.length }} selected</span>
                </div>
                <input v-model="userFilter" class="input" placeholder="Search users…" style="font-size:12px;margin-bottom:6px" />
                <div v-if="filteredEditUsers.length" class="check-list">
                  <label v-for="u in filteredEditUsers" :key="u.id" class="user-check-item" :class="{'user-check-item--sel': assetForm.allowedUserIds.includes(u.id)}">
                    <input type="checkbox" :value="u.id" v-model="assetForm.allowedUserIds" />
                    <div class="user-check-body">
                      <span class="user-check-name">{{ u.username }}</span>
                      <span v-if="u.display_name" class="user-check-sub">{{ u.display_name }}</span>
                    </div>
                  </label>
                </div>
                <div v-else class="muted-note">No users match.</div>
                <div class="hint-line">Selected users get an SSH authorization rule for this host.</div>

                <!-- SSH credentials -->
                <div class="section-head">
                  <div class="asset-form-section-label" style="margin-bottom:0">SSH Credentials</div>
                  <button class="btn-pill" :class="newCred.show ? 'btn-pill-active' : 'btn-pill-outline'" style="font-size:11px" @click="newCred.show ? resetNewCred() : (newCred.show = true)">{{ newCred.show ? '✕ Cancel' : '+ New Credential' }}</button>
                </div>

                <!-- inline create credential -->
                <div v-if="newCred.show" class="inline-cred-form">
                  <div class="form-grid-2">
                    <div class="form-group"><label class="form-label">Name</label><input v-model="newCred.name" class="input" placeholder="optional label" /></div>
                    <div class="form-group"><label class="form-label">Username</label><input v-model="newCred.username" class="input" placeholder="e.g. root" autocomplete="off" /></div>
                    <div class="form-group"><label class="form-label">Type</label><select v-model="newCred.secret_type" class="input"><option value="password">Password</option><option value="ssh_key">SSH Key</option></select></div>
                    <div v-if="newCred.secret_type === 'password'" class="form-group"><label class="form-label">Password</label><input v-model="newCred.password" type="password" class="input" autocomplete="new-password" /></div>
                  </div>
                  <template v-if="newCred.secret_type === 'ssh_key'">
                    <div class="form-group"><label class="form-label">Private Key</label><textarea v-model="newCred.private_key" class="input" rows="4" style="font-family:var(--font-mono);font-size:11px" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea></div>
                    <div class="form-group"><label class="form-label">Passphrase <span style="color:var(--text2);font-weight:400">(optional)</span></label><input v-model="newCred.passphrase" type="password" class="input" autocomplete="new-password" /></div>
                  </template>
                  <div v-if="newCred.error" class="err">{{ newCred.error }}</div>
                  <div style="display:flex;gap:8px;justify-content:flex-end">
                    <button class="btn btn-sm" @click="resetNewCred">Cancel</button>
                    <button class="btn btn-sm btn-primary" :disabled="newCred.saving" @click="createInlineCred">{{ newCred.saving ? 'Creating…' : 'Create & Link' }}</button>
                  </div>
                </div>

                <div v-if="allCredentials.length" class="check-list">
                  <label v-for="c in allCredentials" :key="c.id" class="cred-check-item" :class="{'cred-check-item--sel': assetForm.linkedCredIds.includes(c.id)}">
                    <input type="checkbox" :value="c.id" v-model="assetForm.linkedCredIds" />
                    <div class="cred-item-info">
                      <span class="cred-item-name">{{ c.name }}</span>
                      <span class="cred-item-meta">{{ c.username }} · {{ c.secret_type }}</span>
                    </div>
                  </label>
                </div>
                <div v-else class="muted-note">No credentials yet — use “+ New Credential”.</div>

                <!-- Account operations on this host -->
                <template v-if="editingAsset">
                  <div class="section-head">
                    <div class="asset-form-section-label" style="margin-bottom:0">Account Operations → Host</div>
                  </div>
                  <div v-if="hasOpTemplates" class="push-box">
                    <select v-model="pushForm.subjectCredId" class="input" style="font-size:12px;width:100%">
                      <option value="">— account (credential) —</option>
                      <option v-for="c in allCredentials" :key="c.id" :value="c.id">{{ c.username }} ({{ c.name }})</option>
                    </select>
                    <div class="op-row">
                      <button class="btn btn-sm" :disabled="pushing || !pushForm.subjectCredId" @click="runOp('account_push','Create / push')">＋ Create</button>
                      <button class="btn btn-sm" :disabled="pushing || !pushForm.subjectCredId" @click="runOp('rotate_secret','Rotate secret for')">↻ Rotate</button>
                      <button class="btn btn-sm" style="color:#e3b341" :disabled="pushing || !pushForm.subjectCredId" @click="runOp('disable_account','Disable', true)">⏸ Disable</button>
                      <button class="btn btn-sm" style="color:var(--danger)" :disabled="pushing || !pushForm.subjectCredId" @click="runOp('remove_account','Remove', true)">🗑 Remove</button>
                    </div>
                    <div class="hint-line">Runs against <b>{{ editingAsset.name }}</b>, connecting with a credential linked to this host as the privileged login.</div>
                    <div v-if="pushForm.error" class="err">{{ pushForm.error }}</div>
                  </div>
                  <div v-else class="muted-note">Account operation templates aren’t available yet (automation-service may be starting).</div>
                </template>
              </div>
            </div>

            <div v-if="assetError" class="err" style="margin-top:12px">{{ assetError }}</div>
          </div>
          <div class="side-footer">
            <button class="btn" @click="closeAssetDrawer">Cancel</button>
            <button class="btn btn-primary" @click="saveAsset" :disabled="savingAsset">{{ savingAsset ? 'Saving…' : (editingAsset ? 'Save Changes' : 'Create Asset') }}</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <!-- SIDE PANEL: Access Rules (Users)                                      -->
    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <Teleport to="body">
      <div v-if="udlg.visible" class="side-overlay" @click.self="closeUsersDrawer">
        <div class="side-backdrop" @click="closeUsersDrawer" />
        <div class="side-panel side-panel--wide">
          <div class="side-header">
            <div>
              <span style="font-size:15px">Access Rules</span>
              <span style="font-size:12px;color:var(--text2);margin-left:10px">{{ udlg.host?.name }}</span>
            </div>
            <div style="display:flex;gap:8px">
              <button class="btn btn-primary btn-sm" :disabled="udlg.showAdd || !!udlg.editingId" @click="startAddAuthz">+ Add Rule</button>
              <button class="btn btn-sm btn-icon" @click="closeUsersDrawer">✕</button>
            </div>
          </div>
          <div class="side-body" style="padding:0">
            <table class="table" style="margin:0">
              <thead><tr><th>Rule Name</th><th>User / Group</th><th>Actions</th><th>Credential</th><th>On</th><th style="width:120px"></th></tr></thead>
              <tbody>
                <template v-for="a in dialogAuthzList" :key="a.id">
                  <tr v-if="udlg.editingId !== a.id">
                    <td style="font-weight:500;font-size:13px">{{ a.name }}</td>
                    <td style="font-size:13px">
                      <span v-if="a.user_id">{{ userById.get(a.user_id)?.username || a.user_id }}</span>
                      <span v-else-if="a.user_group_id"><span class="badge badge-blue" style="margin-right:4px;font-size:10px">Group</span>{{ userGroupById.get(a.user_group_id)?.name || a.user_group_id }}</span>
                      <span v-else style="color:var(--text2)">—</span>
                    </td>
                    <td><span v-for="act in a.actions" :key="act" class="badge badge-blue" style="margin-right:3px;font-size:10px">{{ act }}</span></td>
                    <td style="font-size:12px"><span v-if="a.credential_id">{{ credById.get(a.credential_id)?.name || '…' }}</span><span v-else style="color:var(--text2)">any</span></td>
                    <td><span v-if="a.enabled" style="color:#3fb950;font-size:12px">✓</span><span v-else style="color:var(--text2);font-size:12px">—</span></td>
                    <td><div style="display:flex;gap:5px"><button class="btn-pill btn-pill-outline" style="font-size:11px" @click="startEditAuthz(a)">✎ Edit</button><button class="btn-pill btn-pill-outline" style="font-size:11px;color:var(--danger);border-color:var(--danger)" @click="deleteAuthz(a)">✕</button></div></td>
                  </tr>
                  <!-- Inline edit row -->
                  <tr v-else class="inline-edit-row"><td colspan="6"><div class="inline-form">
                    <div class="inline-form-row">
                      <div class="form-group" style="flex:1"><label class="form-label">Rule Name</label><input v-model="udlg.form.name" class="input" /></div>
                      <div class="form-group" style="min-width:120px"><label class="form-label">Target</label><select v-model="udlg.form.targetMode" class="input"><option value="user">User</option><option value="group">Group</option></select></div>
                      <div class="form-group" style="flex:1"><label class="form-label">{{ udlg.form.targetMode === 'user' ? 'User' : 'Group' }}</label>
                        <select v-if="udlg.form.targetMode === 'user'" v-model="udlg.form.user_id" class="input"><option value="">— select —</option><option v-for="u in allUsers" :key="u.id" :value="u.id">{{ u.username }}</option></select>
                        <select v-else v-model="udlg.form.user_group_id" class="input"><option value="">— select —</option><option v-for="g in allUserGroups" :key="g.id" :value="g.id">{{ g.name }}</option></select>
                      </div>
                    </div>
                    <div class="inline-form-row">
                      <div class="form-group" style="flex:1"><label class="form-label">Allowed Actions</label>
                        <div class="actions-check-row"><label v-for="act in KNOWN_ACTIONS" :key="act" class="act-check"><input type="checkbox" :value="act" v-model="udlg.form.actions" />{{ act }}</label></div>
                      </div>
                      <div class="form-group" style="flex:1"><label class="form-label">Credential (optional)</label>
                        <select v-model="udlg.form.credential_id" class="input"><option value="">— Any —</option><option v-for="c in allCredentials" :key="c.id" :value="c.id">{{ c.name }} ({{ c.username }})</option></select>
                      </div>
                      <div class="form-group" style="min-width:80px"><label class="form-label">Enabled</label><select v-model="udlg.form.enabled" class="input"><option :value="true">Yes</option><option :value="false">No</option></select></div>
                    </div>
                    <div v-if="udlg.formError" style="color:var(--danger);font-size:12px">{{ udlg.formError }}</div>
                    <div style="display:flex;gap:8px;justify-content:flex-end"><button class="btn" @click="cancelAuthzEdit">Cancel</button><button class="btn btn-primary" :disabled="udlg.saving" @click="saveAuthz">{{ udlg.saving ? 'Saving…' : 'Save' }}</button></div>
                  </div></td></tr>
                </template>
                <!-- Add new row -->
                <tr v-if="udlg.showAdd" class="inline-edit-row"><td colspan="6"><div class="inline-form">
                  <div class="inline-form-row">
                    <div class="form-group" style="flex:1"><label class="form-label">Rule Name</label><input v-model="udlg.form.name" class="input" placeholder="e.g. dev-ssh-access" /></div>
                    <div class="form-group" style="min-width:120px"><label class="form-label">Target</label><select v-model="udlg.form.targetMode" class="input"><option value="user">User</option><option value="group">Group</option></select></div>
                    <div class="form-group" style="flex:1"><label class="form-label">{{ udlg.form.targetMode === 'user' ? 'User' : 'Group' }}</label>
                      <select v-if="udlg.form.targetMode === 'user'" v-model="udlg.form.user_id" class="input"><option value="">— select —</option><option v-for="u in allUsers" :key="u.id" :value="u.id">{{ u.username }}</option></select>
                      <select v-else v-model="udlg.form.user_group_id" class="input"><option value="">— select —</option><option v-for="g in allUserGroups" :key="g.id" :value="g.id">{{ g.name }}</option></select>
                    </div>
                  </div>
                  <div class="inline-form-row">
                    <div class="form-group" style="flex:1"><label class="form-label">Allowed Actions</label>
                      <div class="actions-check-row"><label v-for="act in KNOWN_ACTIONS" :key="act" class="act-check"><input type="checkbox" :value="act" v-model="udlg.form.actions" />{{ act }}</label></div>
                    </div>
                    <div class="form-group" style="flex:1"><label class="form-label">Credential</label>
                      <select v-model="udlg.form.credential_id" class="input"><option value="">— Any —</option><option v-for="c in allCredentials" :key="c.id" :value="c.id">{{ c.name }} ({{ c.username }})</option></select>
                    </div>
                    <div class="form-group" style="min-width:80px"><label class="form-label">Enabled</label><select v-model="udlg.form.enabled" class="input"><option :value="true">Yes</option><option :value="false">No</option></select></div>
                  </div>
                  <div v-if="udlg.formError" style="color:var(--danger);font-size:12px">{{ udlg.formError }}</div>
                  <div style="display:flex;gap:8px;justify-content:flex-end"><button class="btn" @click="cancelAuthzEdit">Cancel</button><button class="btn btn-primary" :disabled="udlg.saving" @click="saveAuthz">{{ udlg.saving ? 'Saving…' : 'Add Rule' }}</button></div>
                </div></td></tr>
                <tr v-if="!dialogAuthzList.length && !udlg.showAdd"><td colspan="6" style="text-align:center;color:var(--text2);padding:24px;font-size:13px">No access rules for this host.</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <!-- SIDE PANEL: Credentials                                               -->
    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <Teleport to="body">
      <div v-if="cdlg.visible" class="side-overlay" @click.self="closeCredsDrawer">
        <div class="side-backdrop" @click="closeCredsDrawer" />
        <div class="side-panel">
          <div class="side-header">
            <div>
              <span style="font-size:15px">Credentials</span>
              <span style="font-size:12px;color:var(--text2);margin-left:10px">{{ cdlg.host?.name }}</span>
            </div>
            <div style="display:flex;gap:8px">
              <button class="btn btn-primary btn-sm" :disabled="cdlg.showAttach" @click="cdlg.showAttach=true;cdlg.selectedCredIds=[];cdlg.attachError=''">+ Attach</button>
              <button class="btn btn-sm btn-icon" @click="closeCredsDrawer">✕</button>
            </div>
          </div>
          <div class="side-body">
            <!-- Attached credentials list -->
            <table v-if="dialogCredList.length" class="table" style="margin-bottom:16px">
              <thead><tr><th>Name</th><th>Username</th><th>Type</th><th style="width:90px"></th></tr></thead>
              <tbody>
                <tr v-for="c in dialogCredList" :key="c.id">
                  <td style="font-weight:500;font-size:13px">{{ c.name }}</td>
                  <td style="font-size:13px;font-family:var(--font-mono)">{{ c.username }}</td>
                  <td><span class="badge badge-blue" style="font-size:10px">{{ c.secret_type }}</span></td>
                  <td><button class="btn-pill btn-pill-outline" style="font-size:11px;color:var(--danger);border-color:var(--danger)" :disabled="cdlg.unlinkingId === c.id" @click="unlinkCred(c)">{{ cdlg.unlinkingId === c.id ? '…' : '✕ Unlink' }}</button></td>
                </tr>
              </tbody>
            </table>
            <div v-else-if="!cdlg.showAttach" style="text-align:center;color:var(--text2);padding:24px;font-size:13px">No credentials attached.</div>

            <!-- Multi-select attach panel -->
            <div v-if="cdlg.showAttach">
              <div style="font-size:13px;font-weight:600;margin-bottom:10px;color:var(--text)">Select credentials to attach</div>
              <div v-if="availableCredsForAttach.length" class="cred-checkbox-grid">
                <label v-for="c in availableCredsForAttach" :key="c.id" class="cred-check-item">
                  <input type="checkbox" :value="c.id" v-model="cdlg.selectedCredIds" />
                  <div class="cred-item-info">
                    <span class="cred-item-name">{{ c.name }}</span>
                    <span class="cred-item-meta">{{ c.username }} · {{ c.secret_type }}</span>
                  </div>
                </label>
              </div>
              <div v-else style="color:var(--text2);font-size:13px;padding:16px 0">All credentials are already attached.</div>
              <div v-if="cdlg.attachError" style="color:var(--danger);font-size:12px;margin-top:8px">{{ cdlg.attachError }}</div>
              <div style="display:flex;gap:8px;margin-top:14px">
                <button class="btn" @click="cdlg.showAttach=false;cdlg.selectedCredIds=[];cdlg.attachError=''">Cancel</button>
                <button class="btn btn-primary" :disabled="cdlg.attaching || !cdlg.selectedCredIds.length" @click="attachCreds">
                  {{ cdlg.attaching ? 'Attaching…' : `Attach ${cdlg.selectedCredIds.length || ''} Selected` }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <!-- SIDE PANEL: Host detail (Overview · Sessions · Activity)              -->
    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <Teleport to="body">
      <div v-if="hdlg.visible" class="side-overlay" @click.self="closeHostDetail">
        <div class="side-backdrop" @click="closeHostDetail" />
        <div class="side-panel side-panel--wide">
          <div class="side-header">
            <div>
              <span style="font-size:15px">{{ hdlg.host?.name }}</span>
              <span style="font-size:12px;color:var(--text2);margin-left:10px">{{ hdlg.host?.ip }}</span>
            </div>
            <button class="btn btn-sm btn-icon" @click="closeHostDetail">✕</button>
          </div>
          <div class="hd-tabs">
            <button :class="['hd-tab', hdlg.tab==='overview' && 'active']" @click="hdlgTab('overview')">Overview</button>
            <button :class="['hd-tab', hdlg.tab==='sessions' && 'active']" @click="hdlgTab('sessions')">Sessions</button>
            <button :class="['hd-tab', hdlg.tab==='activity' && 'active']" @click="hdlgTab('activity')">Activity</button>
          </div>
          <div class="side-body">
            <!-- Overview -->
            <div v-if="hdlg.tab==='overview'">
              <div class="hd-meta">
                <div class="hd-meta-row"><span class="hd-k">Address</span><span class="ip-mono">{{ hdlg.host?.ip }}:{{ hdlg.host?.port || 22 }}</span></div>
                <div class="hd-meta-row"><span class="hd-k">Platform</span><span>{{ hdlg.host?.os_type === 'windows' ? 'Windows' : 'Linux' }}</span></div>
                <div class="hd-meta-row"><span class="hd-k">Zone</span><span>{{ zoneName(hdlg.host) || '—' }}</span></div>
                <div class="hd-meta-row"><span class="hd-k">Host Groups</span><span>{{ groupNames(hdlg.host).join(', ') || '—' }}</span></div>
                <div class="hd-meta-row"><span class="hd-k">Status</span><span :class="hdlg.host?.enabled ? 'status-ok' : 'status-off'">{{ hdlg.host?.enabled ? 'Active' : 'Inactive' }}</span></div>
                <div class="hd-meta-row"><span class="hd-k">Reachable</span><span>{{ hdlg.host?.is_reachable == null ? '—' : (hdlg.host?.is_reachable ? '✓ yes' : '✗ no') }}</span></div>
              </div>

              <div class="hd-counts">
                <button class="hd-count" @click="openUsersDrawer(hdlg.host)">
                  <span class="hd-count-num">{{ hostAuthzMap.get(hdlg.host?.id)?.length || 0 }}</span>
                  <span class="hd-count-label">Users / access rules ›</span>
                </button>
                <button class="hd-count" @click="openCredsDrawer(hdlg.host)">
                  <span class="hd-count-num">{{ hostCredMap.get(hdlg.host?.id)?.length || 0 }}</span>
                  <span class="hd-count-label">Credentials ›</span>
                </button>
              </div>

              <div v-if="quickTemplates.length" class="hd-section-label">Automation</div>
              <div v-if="quickTemplates.length" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px">
                <button v-for="t in quickTemplates" :key="t.id" class="btn-pill btn-pill-outline" style="font-size:11px" @click="runQuickAction(t, hdlg.host)">▶ {{ t.name }}</button>
              </div>

              <div style="display:flex;gap:8px">
                <button class="btn btn-sm" @click="connectSSH(hdlg.host)" :disabled="!hdlg.host?.enabled">⌗ SSH</button>
                <button v-if="auth.isAdminOrSupport" class="btn btn-sm" @click="editFromDetail">✎ Edit host</button>
                <button v-if="auth.isAdminOrSupport && !hdlg.host?.zabbix_hostid" class="btn btn-sm" style="color:var(--danger)" @click="deleteAsset(hdlg.host); closeHostDetail()">🗑 Delete</button>
                <span v-else-if="auth.isAdminOrSupport" class="btn btn-sm" style="opacity:0.4;cursor:not-allowed" title="Zabbix-synced hosts can't be deleted here — remove in Zabbix and re-sync">🗑 Delete</span>
              </div>
            </div>

            <!-- Sessions -->
            <div v-else-if="hdlg.tab==='sessions'">
              <div v-if="hdlg.sessionsLoading" style="padding:24px;text-align:center;color:var(--text2)">Loading…</div>
              <table v-else-if="hdlg.sessions.length" class="table" style="margin:0">
                <thead><tr><th>User</th><th>Client IP</th><th>Started</th><th>Ended</th><th>Status</th></tr></thead>
                <tbody>
                  <tr v-for="s in hdlg.sessions" :key="s.id">
                    <td style="font-size:13px">{{ s.username || '—' }}</td>
                    <td class="ip-mono" style="font-size:12px">{{ s.client_ip || '—' }}</td>
                    <td style="font-size:12px;color:var(--text2)">{{ fmtTs(s.started_at) }}</td>
                    <td style="font-size:12px;color:var(--text2)">{{ s.ended_at ? fmtTs(s.ended_at) : '—' }}</td>
                    <td><span class="badge" :class="sessionBadge(s.status)">{{ s.status }}</span></td>
                  </tr>
                </tbody>
              </table>
              <div v-else style="padding:24px;text-align:center;color:var(--text2);font-size:13px">No SSH sessions recorded for this host.</div>
            </div>

            <!-- Activity -->
            <div v-else>
              <div v-if="hdlg.activityLoading" style="padding:24px;text-align:center;color:var(--text2)">Loading…</div>
              <div v-else-if="hdlg.activity.length" class="hd-activity">
                <div v-for="(ev, i) in hdlg.activity" :key="i" class="hd-act-row" :class="{ 'hd-act-row--click': ev.runId }" @click="ev.runId && router.push(`/jobs/${ev.runId}`)">
                  <span class="hd-act-icon">{{ ev.icon }}</span>
                  <div style="flex:1;min-width:0">
                    <div class="hd-act-label">{{ ev.label }}</div>
                    <div class="hd-act-sub">{{ ev.sub }}</div>
                  </div>
                  <span class="hd-act-ts">{{ fmtTs(ev.ts) }}</span>
                </div>
              </div>
              <div v-else style="padding:24px;text-align:center;color:var(--text2);font-size:13px">No recent activity for this host.</div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

  </AppShell>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'
import AsyncPicker, { type PickerItem } from '@/components/common/AsyncPicker.vue'
import api, { getToken, terminalUrl } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useConfirm } from '@/composables/useConfirm'

const router  = useRouter()
const auth    = useAuthStore()
const { confirm } = useConfirm()
const KNOWN_ACTIONS = ['ssh', 'rdp', 'console', 'upload', 'download']

// ── Data ───────────────────────────────────────────────────────────────────
const hosts          = ref<any[]>([])
const zones          = ref<any[]>([])
const hostGroups     = ref<any[]>([])
const loading        = ref(false)
const quickTemplates = ref<any[]>([])
const jobTemplates   = ref<any[]>([])
const syncPending    = ref(false)

// ── Host detail panel (Overview · Sessions · Activity) ──────────────────────
const hdlg = reactive({
  visible: false, host: null as any, tab: 'overview' as 'overview' | 'sessions' | 'activity',
  sessions: [] as any[], sessionsLoading: false,
  activity: [] as any[], activityLoading: false,
})
function openHostDetail(h: any, tab: 'overview' | 'sessions' | 'activity' = 'overview') {
  hdlg.host = h; hdlg.tab = tab; hdlg.visible = true
  hdlg.sessions = []; hdlg.activity = []
  if (tab === 'sessions') loadHostSessions()
  if (tab === 'activity') loadHostActivity()
}
function closeHostDetail() { hdlg.visible = false }
function hdlgTab(t: 'overview' | 'sessions' | 'activity') {
  hdlg.tab = t
  if (t === 'sessions' && !hdlg.sessions.length) loadHostSessions()
  if (t === 'activity' && !hdlg.activity.length) loadHostActivity()
}
function editFromDetail() { const h = hdlg.host; closeHostDetail(); openEditAsset(h) }

async function loadHostSessions() {
  hdlg.sessionsLoading = true
  try {
    hdlg.sessions = (await api.get('/ssh/sessions', { params: { host_id: hdlg.host.id } })).data || []
  } catch { hdlg.sessions = [] }
  finally { hdlg.sessionsLoading = false }
}

async function loadHostActivity() {
  hdlg.activityLoading = true
  const hid = hdlg.host.id
  try {
    const [aud, runs] = await Promise.all([
      api.get('/audit/logs', { params: { limit: 200 } }).then(r => r.data).catch(() => []),
      api.get('/job-runs').then(r => r.data).catch(() => []),
    ])
    const auditItems = (aud || [])
      .filter((l: any) => l.resource_id === hid || (l.details && JSON.stringify(l.details).includes(hid)))
      .map((l: any) => ({ icon: '📝', ts: l.created_at, label: l.action, sub: l.username || '' }))
    const runItems = (runs || [])
      .filter((r: any) => (r.target_host_ids || []).includes(hid))
      .map((r: any) => ({ icon: '⚙', ts: r.started_at, label: `${r.job_template_name || 'job'} — ${r.status}`, sub: r.action_type || '', runId: r.id }))
    hdlg.activity = [...auditItems, ...runItems]
      .sort((a, b) => new Date(b.ts || 0).getTime() - new Date(a.ts || 0).getTime())
      .slice(0, 60)
  } catch { hdlg.activity = [] }
  finally { hdlg.activityLoading = false }
}

function fmtTs(d: string | null): string { return d ? new Date(d).toLocaleString() : '—' }
function sessionBadge(s: string): string {
  if (s === 'active') return 'badge-green'
  if (s === 'error' || s === 'terminated') return 'badge-red'
  return 'badge-gray'
}
const lastSynced     = ref<string | null>(null)

const allAuthorizations = ref<any[]>([])
const allCredentials    = ref<any[]>([])
const allUsers          = ref<any[]>([])
const allUserGroups     = ref<any[]>([])

// ── Filters ────────────────────────────────────────────────────────────────
const search       = ref('')
const osFilter     = ref('')
const sourceFilter = ref('')
const sortKey  = ref<'name' | 'ip' | ''>('')
const sortDir  = ref<1 | -1>(1)

function toggleSort(key: 'name' | 'ip') {
  if (sortKey.value === key) { sortDir.value = sortDir.value === 1 ? -1 : 1 }
  else { sortKey.value = key; sortDir.value = 1 }
}

const filtered = computed(() => {
  const s = search.value.toLowerCase()
  let rows = hosts.value.filter(h => {
    const groupHit = s && groupNames(h).some(g => g.toLowerCase().includes(s))
    return (!s || h.name.toLowerCase().includes(s) || h.ip.includes(s) || groupHit)
      && (!osFilter.value     || h.os_type === osFilter.value)
      && (!sourceFilter.value || (sourceFilter.value === 'zabbix' ? !!h.zabbix_hostid : !h.zabbix_hostid))
  })
  if (sortKey.value) {
    const key = sortKey.value
    rows = [...rows].sort((a, b) => a[key].localeCompare(b[key], undefined, { numeric: true }) * sortDir.value)
  }
  return rows
})

// ── Lookup maps ────────────────────────────────────────────────────────────
const zoneById      = computed(() => new Map(zones.value.map(z => [z.id, z])))
const groupById     = computed(() => new Map(hostGroups.value.map(g => [g.id, g])))
const userById      = computed(() => new Map(allUsers.value.map(u => [u.id, u])))
const userGroupById = computed(() => new Map(allUserGroups.value.map(g => [g.id, g])))
const credById      = computed(() => new Map(allCredentials.value.map(c => [c.id, c])))

const hostAuthzMap = computed(() => {
  const m = new Map<string, any[]>()
  for (const a of allAuthorizations.value) {
    if (a.host_id) { if (!m.has(a.host_id)) m.set(a.host_id, []); m.get(a.host_id)!.push(a) }
  }
  return m
})
const hostCredMap = computed(() => {
  const m = new Map<string, any[]>()
  for (const c of allCredentials.value) {
    for (const hid of (c.host_ids || [])) { if (!m.has(hid)) m.set(hid, []); m.get(hid)!.push(c) }
  }
  return m
})

function zoneName(h: any): string | null { return zoneById.value.get(h.zone_id)?.name ?? null }
function groupNames(h: any): string[] { return (h.group_ids || []).map((id: string) => groupById.value.get(id)?.name).filter(Boolean) }

// ── Data loading ───────────────────────────────────────────────────────────
async function load() {
  loading.value = true
  try {
    const reqs: Promise<any>[] = [api.get('/hosts')]
    if (auth.isAdminOrSupport) {
      reqs.push(api.get('/zones'), api.get('/host-groups'), api.get('/authorizations'), api.get('/credentials'), api.get('/users'), api.get('/users/groups'))
    }
    const res = await Promise.all(reqs)
    hosts.value = res[0].data
    if (auth.isAdminOrSupport) {
      zones.value = res[1].data; hostGroups.value = res[2].data
      allAuthorizations.value = res[3].data; allCredentials.value = res[4].data
      allUsers.value = res[5].data; allUserGroups.value = res[6].data
    }
  } finally { loading.value = false }

  // Job templates (admin only) — used to find an Account Push template. Optional service.
  if (auth.isAdminOrSupport) {
    try { jobTemplates.value = (await api.get('/job-templates')).data } catch { jobTemplates.value = [] }
  }

  try {
    const q = await fetch('/api/v1/job-templates?quick_action=true', {
      headers: { Authorization: `Bearer ${getToken() || ''}` },
    }).then(r => r.ok ? r.json() : []).catch(() => [])
    quickTemplates.value = q
  } catch { /* optional services */ }
}

function fmtSyncTime(iso: string): string {
  return new Date(iso).toLocaleString()
}

async function syncFromZabbix() {
  if (syncPending.value) return
  syncPending.value = true
  try {
    await api.post('/hosts/sync-from-zabbix')
    lastSynced.value = new Date().toISOString()
    await load()
  } catch { /* non-critical */ } finally {
    syncPending.value = false
  }
}

// ── SSH connect ────────────────────────────────────────────────────────────
function connectSSH(h: any) {
  window.open(terminalUrl(`host_id=${encodeURIComponent(h.id)}&autoconnect=1`), '_blank')
}

async function runQuickAction(template: any, host: any) {
  try {
    const r = await fetch(`/api/v1/job-templates/${template.id}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken() || ''}` },
      body: JSON.stringify({ target_host_ids: [host.id] }),
    })
    const d = await r.json()
    if (d.run_id) router.push(`/jobs/${d.run_id}`)
  } catch { alert('Failed to start job run') }
}

// ── Create / Edit Asset — side panel ───────────────────────────────────────
const showAssetDrawer = ref(false)
const editingAsset    = ref<any>(null)
const userFilter      = ref('')
const assetForm = reactive({
  name: '', ip: '', os_type: 'linux', port: 22, zone_id: '',
  enabled: true, groups: [] as PickerItem[],
  date_expired: '',
  allowedUserIds: [] as string[],
  linkedCredIds:  [] as string[],
})
const assetError  = ref('')
const savingAsset = ref(false)

const filteredEditUsers = computed(() => {
  const q = userFilter.value.trim().toLowerCase()
  if (!q) return allUsers.value
  return allUsers.value.filter((u: any) =>
    u.username?.toLowerCase().includes(q) || u.display_name?.toLowerCase().includes(q))
})

// Inline credential creation — secret is sent once, never echoed back.
const newCred = reactive({ show: false, name: '', username: '', secret_type: 'password', password: '', private_key: '', passphrase: '', saving: false, error: '' })
function resetNewCred() { Object.assign(newCred, { show: false, name: '', username: '', secret_type: 'password', password: '', private_key: '', passphrase: '', saving: false, error: '' }) }
async function createInlineCred() {
  newCred.error = ''
  if (!newCred.username.trim()) { newCred.error = 'Username is required.'; return }
  if (newCred.secret_type === 'password' && !newCred.password) { newCred.error = 'Password is required.'; return }
  if (newCred.secret_type === 'ssh_key' && !newCred.private_key.trim()) { newCred.error = 'Private key is required.'; return }
  newCred.saving = true
  try {
    const secret = newCred.secret_type === 'password'
      ? { password: newCred.password }
      : { private_key: newCred.private_key, passphrase: newCred.passphrase }
    const payload = {
      name: newCred.name.trim() || newCred.username.trim(),
      username: newCred.username.trim(),
      secret_type: newCred.secret_type,
      secret,
      host_ids: editingAsset.value ? [editingAsset.value.id] : [],
    }
    const res = await api.post('/credentials', payload)
    allCredentials.value.push(res.data)
    if (!assetForm.linkedCredIds.includes(res.data.id)) assetForm.linkedCredIds.push(res.data.id)
    resetNewCred()
  } catch (e: any) {
    newCred.error = e?.response?.data?.detail || 'Failed to create credential'
  } finally {
    newCred.saving = false
  }
}

// Account operations on a host (Create/Push, Rotate, Disable, Remove) via default templates.
const pushForm = reactive({ subjectCredId: '', error: '' })
const pushing  = ref(false)
const _ACCOUNT_ACTIONS = ['account_push', 'rotate_secret', 'disable_account', 'remove_account']
const opTemplateByAction = computed(() => {
  const m: Record<string, string> = {}
  for (const t of jobTemplates.value) {
    if (_ACCOUNT_ACTIONS.includes(t.action_type) && t.enabled !== false && !m[t.action_type]) m[t.action_type] = t.id
  }
  return m
})
const hasOpTemplates = computed(() => _ACCOUNT_ACTIONS.some(a => opTemplateByAction.value[a]))
async function runOp(action: string, label: string, destructive = false) {
  pushForm.error = ''
  if (!editingAsset.value) return
  const tid = opTemplateByAction.value[action]
  if (!tid) { pushForm.error = `No "${action}" template available.`; return }
  if (!pushForm.subjectCredId) { pushForm.error = 'Select an account credential.'; return }
  if (destructive) {
    const c = allCredentials.value.find((c: any) => c.id === pushForm.subjectCredId)
    if (!await confirm(`${label} account "${c?.username}" on ${editingAsset.value.name}?`, { title: `${label} account`, danger: true, confirmLabel: label })) return
  }
  pushing.value = true
  try {
    const res = await api.post(`/job-templates/${tid}/run`, {
      target_host_ids: [editingAsset.value.id],
      params: { subject_credential_id: pushForm.subjectCredId },
    })
    if (res.data?.run_id) router.push(`/jobs/${res.data.run_id}`)
  } catch (e: any) {
    pushForm.error = e?.response?.data?.detail || 'Failed to start operation'
  } finally {
    pushing.value = false
  }
}

// Inline host-group create from the asset panel.
const newGroupName = ref('')
const creatingGroup = ref(false)
const groupError = ref('')
async function createGroup() {
  const name = newGroupName.value.trim()
  if (!name) return
  creatingGroup.value = true; groupError.value = ''
  try {
    const res = await api.post('/host-groups', { name })
    hostGroups.value.push(res.data)
    assetForm.groups.push({ id: res.data.id, label: res.data.name })
    newGroupName.value = ''
  } catch (e: any) {
    groupError.value = e?.response?.data?.detail || 'Failed to create group'
  } finally {
    creatingGroup.value = false
  }
}

function openCreateAsset() {
  editingAsset.value = null
  // Default expiry: 30 years from today
  const exp = new Date(); exp.setFullYear(exp.getFullYear() + 30)
  const expStr = exp.toISOString().slice(0, 10)
  Object.assign(assetForm, { name: '', ip: '', os_type: 'linux', port: 22, zone_id: '', enabled: true, groups: [], date_expired: expStr, allowedUserIds: [], linkedCredIds: [] })
  userFilter.value = ''; resetNewCred(); Object.assign(pushForm, { subjectCredId: '', error: '' }); newGroupName.value = ''; groupError.value = ''
  assetError.value = ''; showAssetDrawer.value = true
}
function openEditAsset(h: any) {
  editingAsset.value = h
  // Pre-select users who have authorizations for this host
  const authorizedUserIds = allAuthorizations.value.filter(a => a.host_id === h.id && a.user_id).map((a: any) => a.user_id)
  // Pre-select credentials already linked to this host
  const linkedCreds = allCredentials.value.filter(c => (c.host_ids || []).includes(h.id)).map((c: any) => c.id)
  const expStr = h.date_expired ? h.date_expired.slice(0, 10) : ''
  Object.assign(assetForm, {
    name: h.name, ip: h.ip, os_type: h.os_type || 'linux', port: h.port || 22,
    zone_id: h.zone_id || '', enabled: h.enabled, date_expired: expStr,
    groups: (h.group_ids || []).map((id: string) => ({ id, label: groupById.value.get(id)?.name })).filter((g: PickerItem) => g.label),
    allowedUserIds: authorizedUserIds,
    linkedCredIds: linkedCreds,
  })
  userFilter.value = ''; resetNewCred(); Object.assign(pushForm, { subjectCredId: '', error: '' }); newGroupName.value = ''; groupError.value = ''
  assetError.value = ''; showAssetDrawer.value = true
}
function closeAssetDrawer() {
  showAssetDrawer.value = false; editingAsset.value = null
  resetNewCred(); Object.assign(pushForm, { subjectCredId: '', error: '' }); newGroupName.value = ''; groupError.value = ''
}

async function saveAsset() {
  savingAsset.value = true; assetError.value = ''
  try {
    const p: any = {
      name: assetForm.name, ip: assetForm.ip, port: assetForm.port,
      os_type: assetForm.os_type, enabled: assetForm.enabled,
      zone_id: assetForm.zone_id || null,
      group_ids: assetForm.groups.map(g => g.id),
    }
    if (assetForm.date_expired) p.date_expired = assetForm.date_expired

    let hostId: string
    if (editingAsset.value) {
      await api.put(`/hosts/${editingAsset.value.id}`, p)
      hostId = editingAsset.value.id
    } else {
      const res = await api.post('/hosts', p)
      hostId = res.data.id
    }

    // Sync credential links
    if (hostId) {
      await Promise.all(allCredentials.value.map(async (cred: any) => {
        const shouldLink = assetForm.linkedCredIds.includes(cred.id)
        const isLinked   = (cred.host_ids || []).includes(hostId)
        if (shouldLink === isLinked) return
        const newHostIds = shouldLink
          ? [...new Set([...(cred.host_ids || []), hostId])]
          : (cred.host_ids || []).filter((id: string) => id !== hostId)
        await api.put(`/credentials/${cred.id}`, { ...cred, host_ids: newHostIds, secret: {} })
      }))
    }

    // Sync user authorizations (ssh action, enabled)
    if (hostId) {
      const existingAuthz = allAuthorizations.value.filter((a: any) => a.host_id === hostId && a.user_id)
      const existingUserIds = new Set(existingAuthz.map((a: any) => a.user_id))
      const wantedIds = new Set(assetForm.allowedUserIds)
      // Remove revoked
      await Promise.all(existingAuthz
        .filter((a: any) => !wantedIds.has(a.user_id))
        .map((a: any) => api.delete(`/authorizations/${a.id}`))
      )
      // Add new
      await Promise.all(assetForm.allowedUserIds
        .filter(uid => !existingUserIds.has(uid))
        .map(uid => {
          const u = allUsers.value.find((u: any) => u.id === uid)
          return api.post('/authorizations', {
            name: `${u?.username || uid} → ${assetForm.name}`,
            user_id: uid, host_id: hostId,
            actions: ['ssh'], enabled: true,
          })
        })
      )
    }

    new BroadcastChannel('seyalrun-hosts').postMessage({ type: 'hosts-updated' })
    closeAssetDrawer(); load()
  } catch (e: any) {
    assetError.value = e?.response?.data?.detail || 'Failed to save'
  } finally {
    savingAsset.value = false
  }
}

// ── CSV export / import ─────────────────────────────────────────────────
// Deliberately host-attribute-only: no user IDs, no credential IDs/links, no secrets —
// a CSV export is a plain-text file that's easy to end up emailed or committed
// somewhere, so it only ever carries what's already visible on this table.
const CSV_HEADERS = ['Name', 'IP', 'Port', 'Platform', 'Zone', 'Host Groups', 'Status', 'Expires']

function csvEscape(v: string): string {
  return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v
}

function exportHostsCsv() {
  const rows = hosts.value.map((h: any) => [
    h.name || '', h.ip || '', String(h.port ?? ''), h.os_type === 'windows' ? 'Windows' : 'Linux',
    zoneName(h) || '', groupNames(h).join('; '), h.enabled ? 'Active' : 'Inactive', h.date_expired || '',
  ])
  const csv = [CSV_HEADERS, ...rows].map((r) => r.map(csvEscape).join(',')).join('\r\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = `seyalrun-hosts-${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

// Minimal RFC4126-ish CSV line parser: handles quoted fields, escaped "" quotes, and
// commas/newlines inside quotes — a naive split(',') would break on any of those.
function parseCsv(text: string): string[][] {
  const rows: string[][] = []
  let row: string[] = [], field = '', inQuotes = false
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (inQuotes) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++ }
      else if (c === '"') { inQuotes = false }
      else { field += c }
    } else if (c === '"') { inQuotes = true }
    else if (c === ',') { row.push(field); field = '' }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++
      row.push(field); field = ''
      if (row.some((f) => f !== '')) rows.push(row)
      row = []
    } else { field += c }
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row) }
  return rows
}

const csvFileInput = ref<HTMLInputElement | null>(null)
const csvImporting = ref(false)
const csvImportError = ref('')

async function handleCsvFile(e: Event) {
  csvImportError.value = ''
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  try {
    const text = await file.text()
    const rows = parseCsv(text)
    if (!rows.length) { csvImportError.value = 'CSV file is empty.'; return }
    const header = rows[0].map((h) => h.trim().toLowerCase())
    const col = (name: string) => header.indexOf(name)
    const iName = col('name'), iIp = col('ip'), iPort = col('port'), iOs = col('platform')
    const iZone = col('zone'), iGroups = col('host groups'), iStatus = col('status'), iExp = col('expires')
    if (iName === -1) { csvImportError.value = 'CSV must have a "Name" column.'; return }

    const zoneIdByName = new Map(zones.value.map((z: any) => [z.name.toLowerCase(), z.id]))
    const groupIdByName = new Map(hostGroups.value.map((g: any) => [g.name.toLowerCase(), g.id]))
    const existingByName = new Map(hosts.value.map((h: any) => [h.name.toLowerCase(), h]))

    const parsed = rows.slice(1)
      .map((r) => ({
        name: (r[iName] || '').trim(),
        ip: iIp >= 0 ? (r[iIp] || '').trim() : '',
        port: iPort >= 0 ? parseInt(r[iPort], 10) || 22 : 22,
        os_type: iOs >= 0 && (r[iOs] || '').trim().toLowerCase() === 'windows' ? 'windows' : 'linux',
        zone_id: iZone >= 0 ? zoneIdByName.get((r[iZone] || '').trim().toLowerCase()) || null : null,
        group_ids: iGroups >= 0
          ? (r[iGroups] || '').split(';').map((g) => g.trim().toLowerCase()).filter(Boolean)
            .map((g) => groupIdByName.get(g)).filter((id): id is string => !!id)
          : [],
        enabled: iStatus >= 0 ? (r[iStatus] || '').trim().toLowerCase() !== 'inactive' : true,
        date_expired: iExp >= 0 ? (r[iExp] || '').trim() || null : null,
      }))
      .filter((r) => r.name)

    if (!parsed.length) { csvImportError.value = 'No valid rows found (each row needs at least a Name).'; return }

    const toCreate = parsed.filter((r) => !existingByName.has(r.name.toLowerCase()))
    const toUpdate = parsed.filter((r) => existingByName.has(r.name.toLowerCase()))

    // Import only ever creates or updates rows present in the file — hosts that exist in
    // SeyalRun but aren't in the CSV are never touched, let alone deleted. Existing hosts
    // that ARE in the file only get overwritten after this explicit confirmation.
    const summary = `Import ${parsed.length} row(s) from "${file.name}":\n`
      + `• ${toCreate.length} new host(s) will be created\n`
      + `• ${toUpdate.length} existing host(s) will be updated (matched by name) — their current name/IP/port/platform/zone/groups/status/expiry will be overwritten\n`
      + `Hosts not listed in the file are never touched or deleted.`
    const proceed = await confirm(summary, {
      title: 'Confirm CSV Import', danger: toUpdate.length > 0,
      confirmLabel: toUpdate.length > 0 ? `Overwrite ${toUpdate.length} & create ${toCreate.length}` : `Create ${toCreate.length}`,
    })
    if (!proceed) return

    csvImporting.value = true
    let failed = 0
    for (const r of toCreate) {
      const { name, ip, port, os_type, zone_id, group_ids, enabled, date_expired } = r
      try { await api.post('/hosts', { name, ip, port, os_type, zone_id, group_ids, enabled, ...(date_expired ? { date_expired } : {}) }) }
      catch { failed++ }
    }
    for (const r of toUpdate) {
      const existing = existingByName.get(r.name.toLowerCase())
      const { name, ip, port, os_type, zone_id, group_ids, enabled, date_expired } = r
      try { await api.put(`/hosts/${existing.id}`, { name, ip, port, os_type, zone_id, group_ids, enabled, ...(date_expired ? { date_expired } : {}) }) }
      catch { failed++ }
    }
    new BroadcastChannel('seyalrun-hosts').postMessage({ type: 'hosts-updated' })
    await load()
    if (failed) csvImportError.value = `Import finished with ${failed} row(s) failed — check host names/fields and retry those rows.`
  } catch (err: any) {
    csvImportError.value = err?.message || 'Failed to read CSV file.'
  } finally {
    csvImporting.value = false
    if (csvFileInput.value) csvFileInput.value.value = ''
  }
}

async function deleteAsset(h: any) {
  if (!await confirm(`Delete host "${h.name}"? This cannot be undone.`, { title: 'Delete Asset', danger: true, confirmLabel: 'Delete' })) return
  try { await api.delete(`/hosts/${h.id}`); load() }
  catch (e: any) { alert(e?.response?.data?.detail || 'Failed to delete') }
}

// ── Bulk select/delete (Zabbix-synced hosts are never selectable) ──────────
const selectedHostIds = ref<Set<string>>(new Set())
const deletableFilteredHosts = computed(() => filtered.value.filter((h: any) => !h.zabbix_hostid))
const allDeletableHostsSelected = computed(() =>
  deletableFilteredHosts.value.length > 0 && deletableFilteredHosts.value.every((h: any) => selectedHostIds.value.has(h.id))
)
function toggleHostSelect(id: string) {
  const s = new Set(selectedHostIds.value)
  s.has(id) ? s.delete(id) : s.add(id)
  selectedHostIds.value = s
}
function toggleSelectAllHosts() {
  selectedHostIds.value = allDeletableHostsSelected.value
    ? new Set()
    : new Set(deletableFilteredHosts.value.map((h: any) => h.id))
}
async function bulkDeleteHosts() {
  const n = selectedHostIds.value.size
  if (!n) return
  if (!await confirm(`Delete ${n} selected host${n === 1 ? '' : 's'}? This cannot be undone.`, { title: 'Delete Assets', danger: true, confirmLabel: 'Delete' })) return
  const failures: string[] = []
  for (const id of selectedHostIds.value) {
    try { await api.delete(`/hosts/${id}`) }
    catch (e: any) { failures.push(e?.response?.data?.detail || id) }
  }
  selectedHostIds.value = new Set()
  load()
  if (failures.length) alert(`${failures.length} of ${n} failed:\n${failures.join('\n')}`)
}
async function searchHostGroups(query: string): Promise<PickerItem[]> {
  const sel = new Set(assetForm.groups.map(g => g.id)); const q = query.trim().toLowerCase()
  return hostGroups.value.filter(g => !sel.has(g.id) && (!q || g.name.toLowerCase().includes(q))).slice(0, 20).map(g => ({ id: g.id, label: g.name, sublabel: g.description }))
}

// ── Users / Authz drawer ───────────────────────────────────────────────────
const _blankF = () => ({ name: '', targetMode: 'user' as 'user' | 'group', user_id: '', user_group_id: '', actions: ['ssh'] as string[], credential_id: '', enabled: true })
const udlg = reactive({ visible: false, host: null as any, editingId: null as string | null, showAdd: false, form: _blankF(), saving: false, formError: '' })
const dialogAuthzList = computed(() => hostAuthzMap.value.get(udlg.host?.id) || [])

function openUsersDrawer(h: any) { udlg.host = h; udlg.editingId = null; udlg.showAdd = false; Object.assign(udlg.form, _blankF()); udlg.formError = ''; udlg.visible = true }
function closeUsersDrawer() { udlg.visible = false; udlg.editingId = null; udlg.showAdd = false }
function startAddAuthz() { udlg.editingId = null; udlg.showAdd = true; Object.assign(udlg.form, _blankF()); udlg.formError = '' }
function startEditAuthz(a: any) { udlg.showAdd = false; udlg.editingId = a.id; Object.assign(udlg.form, { name: a.name, targetMode: a.user_id ? 'user' : 'group', user_id: a.user_id || '', user_group_id: a.user_group_id || '', actions: [...(a.actions || ['ssh'])], credential_id: a.credential_id || '', enabled: a.enabled }); udlg.formError = '' }
function cancelAuthzEdit() { udlg.editingId = null; udlg.showAdd = false; udlg.formError = '' }
async function saveAuthz() {
  udlg.formError = ''
  if (!udlg.form.name.trim()) { udlg.formError = 'Rule name required.'; return }
  if (udlg.form.targetMode === 'user' && !udlg.form.user_id) { udlg.formError = 'Select a user.'; return }
  if (udlg.form.targetMode === 'group' && !udlg.form.user_group_id) { udlg.formError = 'Select a group.'; return }
  if (!udlg.form.actions.length) { udlg.formError = 'Select at least one action.'; return }
  udlg.saving = true
  try {
    const p = { name: udlg.form.name.trim(), user_id: udlg.form.targetMode === 'user' ? udlg.form.user_id : null, user_group_id: udlg.form.targetMode === 'group' ? udlg.form.user_group_id : null, host_id: udlg.host.id, credential_id: udlg.form.credential_id || null, actions: udlg.form.actions, enabled: udlg.form.enabled }
    udlg.editingId ? await api.put(`/authorizations/${udlg.editingId}`, p) : await api.post('/authorizations', p)
    allAuthorizations.value = (await api.get('/authorizations')).data
    cancelAuthzEdit()
  } catch (e: any) { udlg.formError = e?.response?.data?.detail || 'Failed'
  } finally { udlg.saving = false }
}
async function deleteAuthz(a: any) {
  if (!await confirm(`Delete rule "${a.name}"?`, { title: 'Delete Rule', danger: true, confirmLabel: 'Delete' })) return
  try { await api.delete(`/authorizations/${a.id}`); allAuthorizations.value = (await api.get('/authorizations')).data }
  catch (e: any) { alert(e?.response?.data?.detail || 'Failed') }
}

// ── Credentials drawer (multi-select) ─────────────────────────────────────
const cdlg = reactive({ visible: false, host: null as any, showAttach: false, selectedCredIds: [] as string[], attaching: false, attachError: '', unlinkingId: null as string | null })
const dialogCredList = computed(() => hostCredMap.value.get(cdlg.host?.id) || [])
const availableCredsForAttach = computed(() => {
  const att = new Set((hostCredMap.value.get(cdlg.host?.id) || []).map((c: any) => c.id))
  return allCredentials.value.filter(c => !att.has(c.id))
})

function openCredsDrawer(h: any) { cdlg.host = h; cdlg.showAttach = false; cdlg.selectedCredIds = []; cdlg.attachError = ''; cdlg.unlinkingId = null; cdlg.visible = true }
function closeCredsDrawer() { cdlg.visible = false }
async function unlinkCred(c: any) {
  if (!await confirm(`Unlink "${c.name}" from ${cdlg.host?.name}?`, { title: 'Unlink Credential', danger: true, confirmLabel: 'Unlink' })) return
  cdlg.unlinkingId = c.id
  try { await api.put(`/credentials/${c.id}`, { ...c, host_ids: (c.host_ids || []).filter((id: string) => id !== cdlg.host?.id), secret: {} }); allCredentials.value = (await api.get('/credentials')).data }
  catch (e: any) { alert(e?.response?.data?.detail || 'Failed') }
  finally { cdlg.unlinkingId = null }
}
async function attachCreds() {
  cdlg.attachError = ''
  if (!cdlg.selectedCredIds.length) { cdlg.attachError = 'Select at least one credential.'; return }
  cdlg.attaching = true
  try {
    await Promise.all(cdlg.selectedCredIds.map(credId => {
      const cred = credById.value.get(credId)
      if (!cred) return Promise.resolve()
      return api.put(`/credentials/${cred.id}`, { ...cred, host_ids: [...new Set([...(cred.host_ids || []), cdlg.host?.id])], secret: {} })
    }))
    allCredentials.value = (await api.get('/credentials')).data
    cdlg.showAttach = false; cdlg.selectedCredIds = []
  } catch (e: any) { cdlg.attachError = e?.response?.data?.detail || 'Failed'
  } finally { cdlg.attaching = false }
}

// ── ESC closes the topmost open dialog (innermost sub-form first) ──────────
function handleEsc(e: KeyboardEvent) {
  if (e.key !== 'Escape') return
  if (showAssetDrawer.value && newCred.show) { resetNewCred(); return }
  if (udlg.visible && (udlg.showAdd || udlg.editingId)) { cancelAuthzEdit(); return }
  if (cdlg.visible && cdlg.showAttach) { cdlg.showAttach = false; cdlg.selectedCredIds = []; cdlg.attachError = ''; return }
  if (cdlg.visible) { closeCredsDrawer(); return }
  if (udlg.visible) { closeUsersDrawer(); return }
  if (showAssetDrawer.value) { closeAssetDrawer(); return }
}

onMounted(() => { load(); window.addEventListener('keydown', handleEsc) })
onBeforeUnmount(() => window.removeEventListener('keydown', handleEsc))
</script>

<style scoped>
/* ── Toolbar ──────────────────────────────────────────────────────────────── */
.assets-toolbar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; align-items: center; }

/* ── Table ───────────────────────────────────────────────────────────────── */
.tr--inactive { opacity: 0.55; }
.th-center, .td-center { text-align: center; }

/* ── SSH icon button ─────────────────────────────────────────────────────── */
.ssh-icon-btn {
  display: flex; align-items: center; justify-content: center;
  width: 30px; height: 30px; border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--bg3);
  color: var(--accent2);
  cursor: pointer; transition: all 0.15s;
}
.ssh-icon-btn svg { width: 16px; height: 16px; }
.ssh-icon-btn:hover { background: rgba(59,130,246,0.15); border-color: var(--accent2); }
.ssh-icon-btn:disabled { opacity: 0.35; cursor: not-allowed; }

/* ── Status ─────────────────────────────────────────────────────────────── */
.status-ok { color: #3fb950; font-size: 12.5px; }
.status-off { color: var(--text2); font-size: 12.5px; }

/* ── Source badges ──────────────────────────────────────────────────────── */
.src-badge {
  display: inline-flex; align-items: center; justify-content: center;
  width: 16px; height: 16px; border-radius: 3px;
  font-size: 9px; font-weight: 800; line-height: 1; flex-shrink: 0;
}
.src-badge--zbx { background: rgba(240,136,62,0.15); border: 1px solid rgba(240,136,62,0.5); color: #f0883e; }
.src-badge--sr  { background: rgba(88,166,255,0.12); border: 1px solid rgba(88,166,255,0.35); color: #58a6ff; }

/* ── Sortable column headers ────────────────────────────────────────────── */
.th-sort { cursor: pointer; user-select: none; }
.th-sort:hover { color: var(--text); }
.sort-arrow { font-size: 9px; margin-left: 3px; color: var(--accent, #58a6ff); }

/* ── Count links ────────────────────────────────────────────────────────── */
.count-link { display: inline-flex; align-items: center; gap: 3px; background: none; border: none; cursor: pointer; padding: 3px 8px; border-radius: 20px; font-size: 12px; color: #58a6ff; transition: background 0.15s; }
.count-link:hover { background: rgba(88,166,255,0.12); }
.count-link--zero { color: var(--text2); }
.count-link--zero:hover { color: #58a6ff; }
.count-link-num { font-weight: 700; font-size: 13px; }
.count-link-label { font-size: 11px; opacity: 0.85; }

/* ── Host name link + detail panel (Overview · Sessions · Activity) ─────── */
.host-name-link { color: var(--text); cursor: pointer; border-bottom: 1px dashed transparent; }
.host-name-link:hover { color: #58a6ff; border-bottom-color: rgba(88,166,255,0.5); }
.hd-tabs { display: flex; gap: 2px; padding: 0 16px; border-bottom: 1px solid var(--border); background: var(--bg2); flex-shrink: 0; }
.hd-tab { padding: 10px 14px; font-size: 13px; font-weight: 600; background: none; border: none; border-bottom: 2px solid transparent; color: var(--text2); cursor: pointer; }
.hd-tab:hover { color: var(--text); }
.hd-tab.active { color: #58a6ff; border-bottom-color: #58a6ff; }
.hd-meta { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 18px; margin-bottom: 16px; }
.hd-meta-row { display: flex; flex-direction: column; gap: 2px; font-size: 13px; }
.hd-k { font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text2); }
.hd-counts { display: flex; gap: 12px; margin-bottom: 16px; }
.hd-count { flex: 1; display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 12px 14px; background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; cursor: pointer; transition: border-color 0.15s; }
.hd-count:hover { border-color: #58a6ff; }
.hd-count-num { font-size: 22px; font-weight: 700; color: var(--text); }
.hd-count-label { font-size: 12px; color: #58a6ff; }
.hd-section-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text2); margin: 4px 0 8px; }
.hd-activity { display: flex; flex-direction: column; }
.hd-act-row { display: flex; align-items: center; gap: 10px; padding: 9px 6px; border-bottom: 1px solid var(--border); }
.hd-act-row--click { cursor: pointer; }
.hd-act-row--click:hover { background: rgba(88,166,255,0.06); }
.hd-act-icon { font-size: 14px; opacity: 0.8; }
.hd-act-label { font-size: 13px; font-weight: 500; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hd-act-sub { font-size: 11px; color: var(--text2); }
.hd-act-ts { font-size: 11px; color: var(--text2); white-space: nowrap; }

/* ── Side panel (right-anchored, compact, responsive) ───────────────────── */
.side-overlay {
  position: fixed; inset: 0; z-index: 100;
  display: flex; align-items: center; justify-content: flex-end;
  padding: 24px;
}
.side-backdrop { position: absolute; inset: 0; background: rgba(0,0,0,0.45); }
.side-panel {
  position: relative; z-index: 1;
  width: min(560px, 94vw);
  max-height: calc(100vh - 48px);
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: 0 16px 50px rgba(0,0,0,0.5);
  display: flex; flex-direction: column;
  animation: side-in 0.2s ease;
}
.side-panel--wide { width: min(960px, 94vw); }
@keyframes side-in { from { transform: translateX(28px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
.side-header {
  padding: 15px 18px; border-bottom: 1px solid var(--border);
  font-weight: 600; font-size: 14px;
  display: flex; align-items: center; justify-content: space-between; flex-shrink: 0;
}
.side-body { padding: 18px; overflow-y: auto; flex: 1; }
.side-footer { padding: 12px 18px; border-top: 1px solid var(--border); display: flex; gap: 8px; justify-content: flex-end; flex-shrink: 0; }

@media (max-width: 560px) {
  .side-overlay { padding: 0; align-items: stretch; }
  .side-panel, .side-panel--wide { width: 100vw; max-height: 100vh; height: 100vh; border-radius: 0; border: none; }
}

/* ── Edit panel: two-column compact layout ──────────────────────────────── */
.edit-cols { display: grid; grid-template-columns: minmax(260px, 1fr) minmax(300px, 1.05fr); gap: 22px; }
@media (max-width: 820px) { .edit-cols { grid-template-columns: 1fr; gap: 18px; } }
.edit-col { min-width: 0; }
.form-grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 480px) { .form-grid-2 { grid-template-columns: 1fr; } }

.section-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 16px; margin-bottom: 8px; }
.btn-pill-active { background: rgba(88,166,255,0.16); border-color: rgba(88,166,255,0.45); color: #58a6ff; }
.btn-pill-active:hover { background: rgba(88,166,255,0.26); }
.pill-count { font-size: 11px; color: #58a6ff; background: rgba(88,166,255,0.12); border-radius: 20px; padding: 2px 8px; }
.hint-line { font-size: 11px; color: var(--text2); margin-top: 5px; }
.err { color: var(--danger); font-size: 12px; margin-top: 4px; }
.muted-note { font-size: 12px; color: var(--text2); background: var(--bg3); border: 1px dashed var(--border); border-radius: 6px; padding: 9px 11px; }

/* ── Scrollable compact check list ──────────────────────────────────────── */
.check-list { display: flex; flex-direction: column; gap: 6px; max-height: 210px; overflow-y: auto; padding-right: 3px; }

/* ── Inline credential create ───────────────────────────────────────────── */
.inline-cred-form {
  background: var(--bg3); border: 1px solid var(--border); border-radius: 8px;
  padding: 12px; margin-bottom: 10px; display: flex; flex-direction: column; gap: 9px;
}

/* ── Push account ───────────────────────────────────────────────────────── */
.push-box { background: var(--bg3); border: 1px solid var(--border); border-radius: 8px; padding: 11px; }
.push-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.op-row { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-top: 8px; }

/* ── Inline edit form (authz table) ─────────────────────────────────────── */
.inline-edit-row td { background: var(--bg3); padding: 14px 16px !important; }
.inline-form { display: flex; flex-direction: column; gap: 10px; }
.inline-form-row { display: flex; gap: 12px; flex-wrap: wrap; }
.inline-form-row .form-group { margin-bottom: 0; }
.actions-check-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px; }
.act-check { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: var(--text); cursor: pointer; padding: 3px 8px; border-radius: 4px; border: 1px solid var(--border); background: var(--bg3); user-select: none; }
.act-check:hover { border-color: var(--accent2); }
.act-check input { accent-color: var(--accent2); cursor: pointer; }

/* ── Asset form section label ───────────────────────────────────────────── */
.asset-form-section-label { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: var(--text2); margin-bottom: 8px; }

/* ── User check items ───────────────────────────────────────────────────── */
.user-check-item {
  display: flex; align-items: center; gap: 8px; padding: 6px 10px;
  border-radius: 6px; border: 1px solid var(--border); background: var(--bg3); cursor: pointer;
  transition: border-color 0.12s, background 0.12s;
}
.user-check-item:hover     { background: rgba(88,166,255,0.07); border-color: rgba(88,166,255,0.25); }
.user-check-item--sel      { background: rgba(88,166,255,0.1); border-color: rgba(88,166,255,0.4) !important; }
.user-check-item input[type="checkbox"] { accent-color: var(--accent2); width: 14px; height: 14px; flex-shrink: 0; cursor: pointer; }
.user-check-body { display: flex; flex-direction: column; min-width: 0; }
.user-check-name { font-size: 12px; font-weight: 600; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.user-check-sub  { font-size: 10px; color: var(--text2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ── Credential checkbox grid + items ───────────────────────────────────── */
.cred-checkbox-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px; }
.cred-check-item--sel { border-color: rgba(88,166,255,0.4) !important; background: rgba(88,166,255,0.1); }
.cred-check-item {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 11px; border-radius: 6px;
  border: 1px solid var(--border); background: var(--bg3);
  cursor: pointer; transition: border-color 0.15s;
}
.cred-check-item:hover { border-color: var(--accent2); }
.cred-check-item input[type="checkbox"] { accent-color: var(--accent2); width: 15px; height: 15px; flex-shrink: 0; cursor: pointer; }
.cred-item-info { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.cred-item-name { font-size: 13px; font-weight: 600; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cred-item-meta { font-size: 11px; color: var(--text2); }
</style>
