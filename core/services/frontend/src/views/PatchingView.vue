<template>
  <AppShell>
    <div class="page">
      <div class="ph">
        <div>
          <h1 class="h1">Patching</h1>
          <p class="sub">Plan, approve and run package updates across your Linux hosts in waves.</p>
        </div>
        <div class="acts">
          <button class="btn ib" type="button" aria-label="Refresh" title="Refresh" @click="loadTab(tab)">↺</button>
          <button v-if="tab === 'rollouts' && auth.isAdmin && !unavailable" class="btn p" type="button" @click="openRollout()">New rollout</button>
          <button v-if="tab === 'baselines' && auth.isAdmin && !unavailable" class="btn p" type="button" @click="openBaseline()">New baseline</button>
          <button v-if="tab === 'windows' && auth.isAdmin && !unavailable" class="btn p" type="button" @click="openWindow()">New window</button>
          <button v-if="tab === 'mirrors' && auth.isAdmin && !unavailable" class="btn p" type="button" @click="openMirror()">New mirror</button>
        </div>
      </div>

      <div v-if="unavailable" class="card"><div class="empty-state">
        <b>Posture service is not available</b>
        The posture service is not deployed or not reachable yet.
        <div style="margin-top:12px"><button class="btn" type="button" @click="loadTab(tab)">Retry</button></div>
      </div></div>

      <div v-else class="stack">
        <div class="tabs" role="tablist">
          <button v-for="t in TABS" :key="t.k" role="tab" :aria-selected="tab === t.k" :class="['tab', { on: tab === t.k }]" @click="setTab(t.k)">{{ t.l }}</button>
        </div>
        <div v-if="error" class="callout" style="--c:var(--bad)" role="alert">{{ error }}</div>
        <div v-if="notice" class="callout" style="--c:var(--ok)" role="status">{{ notice }}</div>

        <!-- Rollouts -->
        <section v-if="tab === 'rollouts'" class="card">
          <div class="bar">
            <label class="vh" for="r-status">Status</label>
            <select id="r-status" v-model="rStatus" class="in" style="width:auto" @change="loadRollouts"><option value="">All statuses</option><option v-for="s in ROLLOUT_STATUSES" :key="s" :value="s">{{ label(s) }}</option></select>
          </div>
          <div class="tw"><table class="t">
            <thead><tr><th>Name</th><th>Status</th><th class="r">Hosts</th><th>Wave</th><th>Counts</th><th>Waiting</th><th>Requested by</th><th>Approved by</th><th>Created</th></tr></thead>
            <tbody>
              <tr v-for="x in rollouts" :key="x.id" style="cursor:pointer" tabindex="0" @click="router.push(`/patching/${x.id}`)" @keydown.enter="router.push(`/patching/${x.id}`)">
                <td><span class="nm">{{ x.name }}</span></td>
                <td><span class="b" :class="rolloutKind(x.status)">{{ label(x.status) }}</span></td>
                <td class="r num">{{ x.hosts }}</td>
                <td class="num">{{ Math.min(x.current_wave + 1, x.waves) }} / {{ x.waves }}</td>
                <td><span v-for="(n, k) in x.counts" :key="k" class="tag" style="margin-right:4px">{{ label(String(k)) }} {{ n }}</span><span v-if="!Object.keys(x.counts || {}).length" class="muted">—</span></td>
                <td style="white-space:normal;max-width:280px"><span v-if="x.waiting">{{ x.waiting }}</span><span v-else-if="x.status_reason" class="muted">{{ x.status_reason }}</span><span v-else class="muted">—</span></td>
                <td>{{ x.created_by_name || '—' }}</td>
                <td>{{ x.approved_by_name || '—' }}<span v-if="x.approval && x.approval.startsWith('not required')" class="muted" :title="x.approval"> (no second admin)</span></td>
                <td class="muted">{{ x.created_at ? ago(x.created_at) : '—' }}</td>
              </tr>
            </tbody>
          </table></div>
          <div v-if="loading" class="empty-state">Loading…</div>
          <div v-else-if="!rollouts.length" class="empty-state"><b>No rollouts</b>{{ rStatus ? 'Nothing has this status.' : auth.isAdmin ? 'Create a rollout to patch hosts in waves.' : 'No rollouts have been created yet.' }}</div>
        </section>

        <!-- Baselines -->
        <section v-else-if="tab === 'baselines'" class="card">
          <div class="tw"><table class="t">
            <thead><tr><th>Name</th><th>Scope</th><th>Reboot</th><th>Waves</th><th>Groups</th><th>Updated</th><th class="r"><span class="vh">Actions</span></th></tr></thead>
            <tbody>
              <tr v-for="b in baselines" :key="b.id">
                <td><span class="nm">{{ b.name }}</span><span v-if="b.description" class="s2" style="white-space:normal;max-width:360px">{{ b.description }}</span></td>
                <td><span class="tag">{{ b.config?.scope }}</span></td>
                <td>{{ label(b.config?.reboot || '') }}</td>
                <td class="muted">canary {{ b.config?.canary_size }}, then {{ b.config?.second_percent }}%, batches of {{ b.config?.batch_size }}</td>
                <td><span v-for="g in b.group_ids" :key="g" class="tag" style="margin-right:4px">{{ groupName(g) }}</span><span v-if="!b.group_ids.length" class="muted">—</span></td>
                <td class="muted">{{ b.updated_at ? ago(b.updated_at) : '—' }}</td>
                <td class="r"><template v-if="auth.isAdmin"><button class="btn sm" type="button" @click="openBaseline(b)">Edit</button> <button class="btn sm d" type="button" @click="removeItem('patch-baselines', b.id, b.name, 'baseline', loadBaselines)">Delete</button></template></td>
              </tr>
            </tbody>
          </table></div>
          <div v-if="loading" class="empty-state">Loading…</div>
          <div v-else-if="!baselines.length" class="empty-state"><b>No baselines</b>A baseline is a saved set of patching settings you can reuse for rollouts.</div>
        </section>

        <!-- Windows -->
        <section v-else-if="tab === 'windows'" class="card">
          <div class="tw"><table class="t">
            <thead><tr><th>Name</th><th>Days</th><th>Start</th><th>Duration</th><th>State</th><th>Next opens</th><th class="r"><span class="vh">Actions</span></th></tr></thead>
            <tbody>
              <tr v-for="w in windows" :key="w.id">
                <td><span class="nm">{{ w.name }}</span></td>
                <td>{{ dayList(w.days) }}</td>
                <td class="mono">{{ w.start }} <span class="muted">{{ w.timezone }}</span></td>
                <td class="num">{{ w.duration_minutes }} min</td>
                <td><span v-if="w.open_now" class="b ok">Open now</span><span v-else class="b off">Closed</span><span v-if="w.open_now && w.open_until" class="s2">until {{ when(w.open_until) }}</span></td>
                <td class="muted">{{ when(w.next_open) }}</td>
                <td class="r"><template v-if="auth.isAdmin"><button class="btn sm" type="button" @click="openWindow(w)">Edit</button> <button class="btn sm d" type="button" @click="removeItem('patch-windows', w.id, w.name, 'window', loadWindows)">Delete</button></template></td>
              </tr>
            </tbody>
          </table></div>
          <div v-if="loading" class="empty-state">Loading…</div>
          <div v-else-if="!windows.length" class="empty-state"><b>No maintenance windows</b>A window says when a rollout may touch hosts.</div>
        </section>

        <!-- Mirrors -->
        <section v-else-if="tab === 'mirrors'" class="card">
          <div class="tw"><table class="t">
            <thead><tr><th>Slug</th><th>Family</th><th>URL</th><th>Suite</th><th>Groups</th><th>State</th><th class="r"><span class="vh">Actions</span></th></tr></thead>
            <tbody>
              <tr v-for="m in mirrors" :key="m.id">
                <td><span class="nm">{{ m.slug }}</span></td>
                <td><span class="tag">{{ m.family }}</span></td>
                <td class="mono">{{ m.base_url }}</td>
                <td class="mono">{{ m.suite || '—' }}<span v-if="m.components" class="muted"> {{ m.components }}</span></td>
                <td><span v-for="g in m.group_ids" :key="g" class="tag" style="margin-right:4px">{{ groupName(g) }}</span><span v-if="!m.group_ids.length" class="muted">—</span></td>
                <td><span v-if="m.enabled" class="b ok">Enabled</span><span v-else class="b off">Disabled</span></td>
                <td class="r"><template v-if="auth.isAdmin"><button class="btn sm" type="button" @click="openMirror(m)">Edit</button> <button class="btn sm d" type="button" @click="removeItem('patch-repos', m.id, m.slug, 'mirror', loadMirrors)">Delete</button></template></td>
              </tr>
            </tbody>
          </table></div>
          <div v-if="loading" class="empty-state">Loading…</div>
          <div v-else-if="!mirrors.length" class="empty-state"><b>No mirrors</b>Without a mirror, hosts patch from their own configured repositories.</div>
        </section>

        <!-- Package operations -->
        <section v-else class="card">
          <PackageOpsTable :ops="pkgOps" :host-names="hostNames" />
          <div v-if="loading" class="empty-state">Loading…</div>
        </section>
      </div>
    </div>

    <!-- New rollout -->
    <div v-if="ro" class="modal-overlay" @click.self="ro = null">
      <div class="modal" role="dialog" aria-label="New rollout" style="width:min(760px,96vw)">
        <div class="modal-header"><span>New rollout</span><button class="btn sm ib g" type="button" aria-label="Close" @click="ro = null">×</button></div>
        <div class="modal-body stack" style="gap:14px;max-height:72vh;overflow:auto">
          <template v-if="!roResult">
            <div><label for="ro-name" class="lbl">Name</label><input id="ro-name" v-model="ro.name" class="in" maxlength="200" style="width:100%" /></div>
            <div><label for="ro-base" class="lbl">Baseline <span class="muted">(optional)</span></label>
              <select id="ro-base" v-model="ro.baseline_id" class="in" style="width:100%"><option value="">None (built-in defaults)</option><option v-for="b in baselines" :key="b.id" :value="b.id">{{ b.name }}</option></select></div>
            <div v-if="ro.finding_ids.length" class="callout" role="status">Only the package behind the chosen finding is updated, on the host it was found on.</div>
            <div v-if="!ro.finding_ids.length">
              <span class="lbl">Hosts</span>
              <input v-model="ro.hostFilter" class="in" placeholder="Filter hosts" aria-label="Filter hosts" style="width:100%;margin-bottom:6px" />
              <div class="card pick"><label v-for="h in pickHosts" :key="h.host_id" class="row" style="gap:8px;font-size:13px"><input v-model="ro.host_ids" type="checkbox" class="chk" :value="h.host_id" /> {{ h.hostname }}</label><div v-if="!pickHosts.length" class="muted">No hosts.</div></div>
              <div class="hint">{{ ro.host_ids.length }} selected</div>
            </div>
            <div v-if="!ro.finding_ids.length">
              <span class="lbl">Host groups <span class="muted">(adds every host in the group)</span></span>
              <div class="card pick"><label v-for="g in groups" :key="g.id" class="row" style="gap:8px;font-size:13px"><input v-model="ro.group_ids" type="checkbox" class="chk" :value="g.id" /> {{ g.name }}</label><div v-if="!groups.length" class="muted">No groups.</div></div>
            </div>
            <div>
              <label for="ro-win" class="lbl">Maintenance window</label>
              <select id="ro-win" v-model="ro.window_id" class="in" style="width:100%" :disabled="ro.ignore_window"><option value="">Select a window…</option><option v-for="w in windows" :key="w.id" :value="w.id">{{ w.name }} ({{ dayList(w.days) }} {{ w.start }} {{ w.timezone }})</option></select>
              <label class="row" style="gap:8px;margin-top:8px;font-size:13.5px"><input v-model="ro.ignore_window" type="checkbox" class="chk" /> Run as soon as approved (no maintenance window)</label>
            </div>
            <fieldset class="fs">
              <legend>Override baseline settings (optional)</legend>
              <div class="grid2" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px">
                <div v-if="!ro.finding_ids.length"><label for="ro-scope" class="lbl">Scope</label><select id="ro-scope" v-model="ro.scope" class="in" style="width:100%"><option value="">From baseline</option><option v-for="s in SCOPES.filter((x) => x !== 'findings')" :key="s" :value="s">{{ label(s) }}</option></select></div>
                <div><label for="ro-reboot" class="lbl">Reboot</label><select id="ro-reboot" v-model="ro.reboot" class="in" style="width:100%"><option value="">From baseline</option><option v-for="s in REBOOTS" :key="s" :value="s">{{ label(s) }}</option></select></div>
              </div>
              <div class="grid2" style="grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin-top:12px">
                <div><label for="ro-inc" class="lbl">Include (one pattern per line)</label><textarea id="ro-inc" v-model="ro.include" class="in mono" rows="3" style="height:auto;padding:8px 10px;width:100%"></textarea></div>
                <div><label for="ro-exc" class="lbl">Exclude (one pattern per line)</label><textarea id="ro-exc" v-model="ro.exclude" class="in mono" rows="3" style="height:auto;padding:8px 10px;width:100%"></textarea></div>
              </div>
              <div class="hint">Include and exclude replace the baseline's lists when filled in. The scope "Named packages" needs at least one include pattern.</div>
            </fieldset>
            <div v-if="roError" class="callout" style="--c:var(--bad)" role="alert">{{ roError }}</div>
          </template>
          <template v-else>
            <div class="callout" style="--c:var(--ok)" role="status">Rollout "{{ roResult.name }}" created with {{ roResult.hosts }} hosts and {{ roResult.packages }} package updates. Status: {{ label(roResult.status) }}.</div>
            <div v-if="roResult.skipped?.length" class="callout" style="--c:var(--warn)" role="status">
              <div><b>{{ roResult.skipped.length }} host{{ roResult.skipped.length === 1 ? ' was' : 's were' }} left out</b>
                <ul style="margin:6px 0 0;padding-left:18px"><li v-for="(s, i) in roResult.skipped" :key="i">{{ s }}</li></ul></div>
            </div>
          </template>
        </div>
        <div class="modal-footer">
          <template v-if="!roResult"><button class="btn" type="button" @click="ro = null">Cancel</button><button class="btn p" type="button" :disabled="!roValid || roBusy" @click="submitRollout">{{ roBusy ? 'Creating…' : 'Create rollout' }}</button></template>
          <template v-else><button class="btn" type="button" @click="ro = null">Close</button><button class="btn p" type="button" @click="router.push(`/patching/${roResult.id}`)">Open rollout</button></template>
        </div>
      </div>
    </div>

    <!-- Baseline editor -->
    <div v-if="bl" class="modal-overlay" @click.self="bl = null">
      <div class="modal" role="dialog" :aria-label="bl.id ? 'Edit baseline' : 'New baseline'" style="width:min(820px,96vw)">
        <div class="modal-header"><span>{{ bl.id ? 'Edit baseline' : 'New baseline' }}</span><button class="btn sm ib g" type="button" aria-label="Close" @click="bl = null">×</button></div>
        <div class="modal-body stack" style="gap:16px;max-height:72vh;overflow:auto">
          <div><label for="bl-name" class="lbl">Name</label><input id="bl-name" v-model="bl.name" class="in" maxlength="200" style="width:100%" /></div>
          <div><label for="bl-desc" class="lbl">Description</label><textarea id="bl-desc" v-model="bl.description" class="in" rows="2" maxlength="2000" style="height:auto;padding:8px 10px;width:100%"></textarea></div>
          <div><span class="lbl">Default host groups <span class="muted">(used when a rollout names no hosts)</span></span>
            <div class="card pick"><label v-for="g in groups" :key="g.id" class="row" style="gap:8px;font-size:13px"><input v-model="bl.group_ids" type="checkbox" class="chk" :value="g.id" /> {{ g.name }}</label><div v-if="!groups.length" class="muted">No groups.</div></div></div>

          <fieldset class="fs"><legend>What to patch</legend>
            <div style="max-width:320px"><label for="bc-scope" class="lbl">Scope</label>
              <select id="bc-scope" v-model="bl.cfg.scope" class="in" style="width:100%"><option v-for="s in SCOPES" :key="s" :value="s">{{ SCOPE_TEXT[s] }}</option></select></div>
            <div class="grid2" style="grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin-top:12px">
              <div><label for="bc-inc" class="lbl">Include (one pattern per line)</label><textarea id="bc-inc" v-model="bl.text.include" class="in mono" rows="3" style="height:auto;padding:8px 10px;width:100%"></textarea></div>
              <div><label for="bc-exc" class="lbl">Exclude (one pattern per line)</label><textarea id="bc-exc" v-model="bl.text.exclude" class="in mono" rows="3" style="height:auto;padding:8px 10px;width:100%"></textarea></div>
            </div>
            <div class="hint">Patterns may use * and ?. "Named packages" needs at least one include pattern. "Findings" only works for rollouts started from findings.</div>
          </fieldset>

          <fieldset class="fs"><legend>Waves</legend>
            <div class="nums"><div v-for="k in WAVE_KEYS" :key="k"><label :for="'bc-' + k" class="lbl">{{ NUM[k].l }}</label><input :id="'bc-' + k" v-model.number="bl.cfg[k]" type="number" class="in" style="width:100%" :min="RANGES[k][0]" :max="RANGES[k][1]" /><div class="hint">{{ RANGES[k][0] }} to {{ RANGES[k][1] }}. {{ NUM[k].h }}</div></div></div>
            <div style="margin-top:12px"><span class="lbl">Cluster groups <span class="muted">(two hosts of one cluster never share a wave)</span></span>
              <div class="card pick"><label v-for="g in groups" :key="g.id" class="row" style="gap:8px;font-size:13px"><input v-model="bl.cfg.cluster_group_ids" type="checkbox" class="chk" :value="g.id" /> {{ g.name }}</label><div v-if="!groups.length" class="muted">No groups.</div></div></div>
          </fieldset>

          <fieldset class="fs"><legend>Checks</legend>
            <div class="nums"><div v-for="k in CHECK_KEYS" :key="k"><label :for="'bc-' + k" class="lbl">{{ NUM[k].l }}</label><input :id="'bc-' + k" v-model.number="bl.cfg[k]" type="number" class="in" style="width:100%" :min="RANGES[k][0]" :max="RANGES[k][1]" /><div class="hint">{{ RANGES[k][0] }} to {{ RANGES[k][1] }}. {{ NUM[k].h }}</div></div></div>
            <label class="row" style="gap:8px;margin-top:12px;font-size:13.5px"><input v-model="bl.cfg.block_on_sessions" type="checkbox" class="chk" /> Stop if someone has an open session on the host</label>
            <div class="grid2" style="grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin-top:12px">
              <div><label for="bc-health" class="lbl">Health check URLs (one per line)</label><textarea id="bc-health" v-model="bl.text.health_urls" class="in mono" rows="3" style="height:auto;padding:8px 10px;width:100%" placeholder="https://host.example/health"></textarea></div>
              <div><label for="bc-ign" class="lbl">Services to ignore (one per line)</label><textarea id="bc-ign" v-model="bl.text.ignore_services" class="in mono" rows="3" style="height:auto;padding:8px 10px;width:100%"></textarea></div>
            </div>
          </fieldset>

          <fieldset class="fs"><legend>Reboot</legend>
            <div class="nums">
              <div><label for="bc-ver" class="lbl">Versions</label><select id="bc-ver" v-model="bl.cfg.versions" class="in" style="width:100%"><option value="exact">Exactly the approved versions</option><option value="at_least">The approved versions or newer</option></select></div>
              <div><label for="bc-reboot" class="lbl">Reboot policy</label><select id="bc-reboot" v-model="bl.cfg.reboot" class="in" style="width:100%"><option v-for="s in REBOOTS" :key="s" :value="s">{{ REBOOT_TEXT[s] }}</option></select></div>
              <div><label for="bc-rbw" class="lbl">{{ NUM.reboot_wait_seconds.l }}</label><input id="bc-rbw" v-model.number="bl.cfg.reboot_wait_seconds" type="number" class="in" style="width:100%" :min="RANGES.reboot_wait_seconds[0]" :max="RANGES.reboot_wait_seconds[1]" /><div class="hint">{{ RANGES.reboot_wait_seconds[0] }} to {{ RANGES.reboot_wait_seconds[1] }}. {{ NUM.reboot_wait_seconds.h }}</div></div>
            </div>
          </fieldset>

          <fieldset class="fs"><legend>Zabbix</legend>
            <div class="nums">
              <div><label for="bc-zbx" class="lbl">Zabbix maintenance</label><select id="bc-zbx" v-model="bl.cfg.zabbix" class="in" style="width:100%"><option v-for="s in ZABBIX" :key="s" :value="s">{{ ZABBIX_TEXT[s] }}</option></select></div>
              <div><label for="bc-zsev" class="lbl">{{ NUM.zabbix_min_severity.l }}</label><input id="bc-zsev" v-model.number="bl.cfg.zabbix_min_severity" type="number" class="in" style="width:100%" min="0" max="5" /><div class="hint">0 to 5. {{ NUM.zabbix_min_severity.h }}</div></div>
            </div>
          </fieldset>

          <fieldset class="fs"><legend>Drain and failure</legend>
            <div class="nums">
              <div><label for="bc-drain" class="lbl">Drain template</label><select id="bc-drain" v-model="bl.cfg.drain_template_id" class="in" style="width:100%"><option :value="null">None</option><option v-for="t in tplOptions" :key="t.id" :value="t.id">{{ t.name }}</option></select></div>
              <div><label for="bc-undrain" class="lbl">Undrain template</label><select id="bc-undrain" v-model="bl.cfg.undrain_template_id" class="in" style="width:100%"><option :value="null">None</option><option v-for="t in tplOptions" :key="t.id" :value="t.id">{{ t.name }}</option></select></div>
            </div>
            <div class="hint">Set both or neither. Templates must be enabled, must not need their own approval, and must be bash or ansible.</div>
            <label class="row" style="gap:8px;margin-top:12px;font-size:13.5px"><input v-model="bl.cfg.rollback_on_failure" type="checkbox" class="chk" /> Roll back a host's packages when it fails</label>
          </fieldset>
          <div v-if="blError" class="callout" style="--c:var(--bad)" role="alert">{{ blError }}</div>
        </div>
        <div class="modal-footer"><button class="btn" type="button" @click="bl = null">Cancel</button><button class="btn p" type="button" :disabled="!bl.name.trim() || blBusy" @click="submitBaseline">{{ blBusy ? 'Saving…' : 'Save' }}</button></div>
      </div>
    </div>

    <!-- Window editor -->
    <div v-if="wn" class="modal-overlay" @click.self="wn = null">
      <div class="modal" role="dialog" :aria-label="wn.id ? 'Edit window' : 'New window'" style="width:min(520px,96vw)">
        <div class="modal-header"><span>{{ wn.id ? 'Edit window' : 'New window' }}</span><button class="btn sm ib g" type="button" aria-label="Close" @click="wn = null">×</button></div>
        <div class="modal-body stack" style="gap:12px">
          <div><label for="wn-name" class="lbl">Name</label><input id="wn-name" v-model="wn.name" class="in" maxlength="200" style="width:100%" /></div>
          <div><label for="wn-tz" class="lbl">Timezone</label><input id="wn-tz" v-model="wn.timezone" class="in" maxlength="64" style="width:100%" placeholder="UTC or Europe/Berlin" /></div>
          <div><span class="lbl">Days</span><div class="row" style="flex-wrap:wrap;gap:6px 14px"><label v-for="(d, i) in DAYS" :key="d" class="row" style="gap:6px;font-size:13.5px"><input v-model="wn.days" type="checkbox" class="chk" :value="i" /> {{ d }}</label></div></div>
          <div class="row" style="gap:12px;align-items:flex-end">
            <div><label for="wn-start" class="lbl">Start (HH:MM)</label><input id="wn-start" v-model="wn.start" class="in" type="time" /></div>
            <div><label for="wn-dur" class="lbl">Duration (minutes)</label><input id="wn-dur" v-model.number="wn.duration_minutes" class="in" type="number" min="15" max="1440" /><div class="hint">15 to 1440</div></div>
          </div>
          <div v-if="wnError" class="callout" style="--c:var(--bad)" role="alert">{{ wnError }}</div>
        </div>
        <div class="modal-footer"><button class="btn" type="button" @click="wn = null">Cancel</button><button class="btn p" type="button" :disabled="!wn.name.trim() || !wn.days.length || !wn.start || wnBusy" @click="submitWindow">{{ wnBusy ? 'Saving…' : 'Save' }}</button></div>
      </div>
    </div>

    <!-- Mirror editor -->
    <div v-if="mr" class="modal-overlay" @click.self="mr = null">
      <div class="modal" role="dialog" :aria-label="mr.id ? 'Edit mirror' : 'New mirror'" style="width:min(640px,96vw)">
        <div class="modal-header"><span>{{ mr.id ? 'Edit mirror' : 'New mirror' }}</span><button class="btn sm ib g" type="button" aria-label="Close" @click="mr = null">×</button></div>
        <div class="modal-body stack" style="gap:12px;max-height:72vh;overflow:auto">
          <div class="row" style="gap:12px;align-items:flex-end;flex-wrap:wrap">
            <div style="flex:1 1 200px"><label for="mr-slug" class="lbl">Slug</label><input id="mr-slug" v-model="mr.slug" class="in" maxlength="40" style="width:100%" placeholder="internal-main" /><div class="hint">Lower-case letters, digits and hyphens.</div></div>
            <div><label for="mr-fam" class="lbl">Family</label><select id="mr-fam" v-model="mr.family" class="in"><option value="deb">deb (Debian, Ubuntu)</option><option value="rpm">rpm (RHEL family)</option><option value="zypp">zypp (SUSE)</option></select></div>
          </div>
          <div><label for="mr-url" class="lbl">Base URL</label><input id="mr-url" v-model="mr.base_url" class="in mono" maxlength="500" style="width:100%" placeholder="https://mirror.example/debian" /><div class="hint">Plain http(s) URL without credentials.</div></div>
          <div v-if="mr.family === 'deb'" class="row" style="gap:12px;flex-wrap:wrap">
            <div style="flex:1 1 200px"><label for="mr-suite" class="lbl">Suite</label><input id="mr-suite" v-model="mr.suite" class="in mono" maxlength="80" style="width:100%" placeholder="bookworm" /></div>
            <div style="flex:1 1 200px"><label for="mr-comp" class="lbl">Components</label><input id="mr-comp" v-model="mr.components" class="in mono" maxlength="200" style="width:100%" placeholder="main contrib" /></div>
          </div>
          <div><label for="mr-key" class="lbl">GPG public key (ASCII-armoured)</label><textarea id="mr-key" v-model="mr.gpg_key" class="in mono" rows="6" maxlength="32768" style="height:auto;padding:8px 10px;width:100%" placeholder="-----BEGIN PGP PUBLIC KEY BLOCK-----"></textarea><div class="hint">Required. Unsigned mirrors are not supported.</div></div>
          <div><span class="lbl">Host groups that use this mirror</span>
            <div class="card pick"><label v-for="g in groups" :key="g.id" class="row" style="gap:8px;font-size:13px"><input v-model="mr.group_ids" type="checkbox" class="chk" :value="g.id" /> {{ g.name }}</label><div v-if="!groups.length" class="muted">No groups.</div></div></div>
          <label class="row" style="gap:8px;font-size:13.5px"><input v-model="mr.enabled" type="checkbox" class="chk" /> Enabled</label>
          <div v-if="mrError" class="callout" style="--c:var(--bad)" role="alert">{{ mrError }}</div>
        </div>
        <div class="modal-footer"><button class="btn" type="button" @click="mr = null">Cancel</button><button class="btn p" type="button" :disabled="!mr.slug.trim() || !mr.base_url.trim() || !mr.gpg_key.trim() || mrBusy" @click="submitMirror">{{ mrBusy ? 'Saving…' : 'Save' }}</button></div>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'
import PackageOpsTable from '@/components/patching/PackageOpsTable.vue'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useConfirm } from '@/composables/useConfirm'
import { ago, dayList, DAYS, detailOf, isDown, label, rolloutKind, when } from '@/composables/patchingUtil'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const { confirm } = useConfirm()

type Tab = 'rollouts' | 'baselines' | 'windows' | 'mirrors' | 'packages'
const TABS: { k: Tab; l: string }[] = [
  { k: 'rollouts', l: 'Rollouts' }, { k: 'baselines', l: 'Baselines' }, { k: 'windows', l: 'Windows' },
  { k: 'mirrors', l: 'Mirrors' }, { k: 'packages', l: 'Package operations' },
]
const isTab = (v: string): v is Tab => TABS.some((t) => t.k === v)
const initTab = String(route.query.tab || 'rollouts')
const tab = ref<Tab>(isTab(initTab) ? initTab : 'rollouts')
function setTab(t: Tab) {
  tab.value = t
  const q = { ...route.query }
  if (t === 'rollouts') delete q.tab; else q.tab = t
  router.replace({ query: q })
}
watch(() => route.query.tab, (t) => { const v = String(t || 'rollouts'); if (isTab(v) && v !== tab.value) tab.value = v })
watch(tab, (t) => loadTab(t))

// Values mirror rollout_core.py (SCOPES, REBOOT_POLICIES, ZABBIX_MODES, _INT_RANGES).
const SCOPES = ['security', 'all', 'findings', 'packages']
const SCOPE_TEXT: Record<string, string> = { security: 'Security updates only', all: 'All updates', findings: 'Packages named by findings', packages: 'Named packages only' }
const REBOOTS = ['never', 'if_required', 'always']
const REBOOT_TEXT: Record<string, string> = { never: 'Never reboot', if_required: 'Reboot if required', always: 'Always reboot' }
const ZABBIX = ['off', 'optional', 'required']
const ZABBIX_TEXT: Record<string, string> = { off: 'Off', optional: 'Optional (continue if Zabbix is unreachable)', required: 'Required (stop if Zabbix fails)' }
const RANGES: Record<string, [number, number]> = {
  canary_size: [1, 100], second_percent: [1, 100], batch_size: [1, 500], concurrency: [1, 100], max_failures: [0, 1000],
  soak_minutes: [0, 1440], reboot_wait_seconds: [60, 3000], min_free_mb_root: [0, 1_000_000], min_free_mb_boot: [0, 1_000_000], zabbix_min_severity: [0, 5],
}
const NUM: Record<string, { l: string; h: string }> = {
  canary_size: { l: 'Canary hosts', h: 'Hosts in the first wave.' },
  second_percent: { l: 'Second wave (%)', h: 'Share of hosts in the second wave.' },
  batch_size: { l: 'Batch size', h: 'Hosts per wave after that.' },
  concurrency: { l: 'Hosts at once', h: 'Hosts patched in parallel in a wave.' },
  max_failures: { l: 'Failures allowed', h: 'The rollout halts when more hosts fail.' },
  soak_minutes: { l: 'Soak (minutes)', h: 'Wait after a wave before the next.' },
  min_free_mb_root: { l: 'Free space on / (MB)', h: 'Minimum before patching.' },
  min_free_mb_boot: { l: 'Free space on /boot (MB)', h: 'Minimum before patching.' },
  reboot_wait_seconds: { l: 'Reboot wait (seconds)', h: 'How long to wait for the host to return.' },
  zabbix_min_severity: { l: 'Minimum problem severity', h: 'Open Zabbix problems at or above this block patching.' },
}
const WAVE_KEYS = ['canary_size', 'second_percent', 'batch_size', 'concurrency', 'max_failures', 'soak_minutes']
const CHECK_KEYS = ['min_free_mb_root', 'min_free_mb_boot']
const LIST_KEYS = ['include', 'exclude', 'health_urls', 'ignore_services']
const ROLLOUT_STATUSES = ['pending_approval', 'approved', 'running', 'paused', 'halted', 'completed', 'rejected', 'cancelled']

const loading = ref(false)
const unavailable = ref(false)
const error = ref('')
const notice = ref('')

const rollouts = ref<any[]>([])
const rStatus = ref('')
const baselines = ref<any[]>([])
const defaults = ref<Record<string, any>>({})
const windows = ref<any[]>([])
const mirrors = ref<any[]>([])
const pkgOps = ref<any[]>([])
const hostList = ref<{ host_id: string; hostname: string }[]>([])
const groups = ref<{ id: string; name: string }[]>([])
const templates = ref<any[]>([])
const groupName = (id: string) => groups.value.find((g) => g.id === id)?.name || id
const hostNames = computed(() => Object.fromEntries(hostList.value.map((h) => [h.host_id, h.hostname])))
// Only bash and ansible templates can drain or undrain a host.
const tplOptions = computed(() => templates.value.filter((t) => ['bash_script', 'ansible_playbook'].includes(t.action_type) && t.enabled !== false))

async function guard(fn: () => Promise<void>, what: string) {
  loading.value = true; error.value = ''; unavailable.value = false
  try { await fn() } catch (e: any) { if (isDown(e)) unavailable.value = true; else error.value = detailOf(e, `Could not load ${what}.`) } finally { loading.value = false }
}
const loadRollouts = () => guard(async () => { rollouts.value = (await api.get('/posture/patches', { params: rStatus.value ? { status: rStatus.value } : {} })).data?.items || [] }, 'rollouts')
const loadBaselines = () => guard(async () => { const d = (await api.get('/posture/patch-baselines')).data; baselines.value = d?.items || []; defaults.value = d?.defaults || {} }, 'baselines')
const loadWindows = () => guard(async () => { windows.value = (await api.get('/posture/patch-windows')).data || [] }, 'windows')
const loadMirrors = () => guard(async () => { mirrors.value = (await api.get('/posture/patch-repos')).data || [] }, 'mirrors')
const loadPkgOps = () => guard(async () => { pkgOps.value = (await api.get('/posture/package-ops', { params: { limit: 50 } })).data || [] }, 'package operations')
function loadTab(t: Tab) {
  if (t === 'rollouts') return loadRollouts()
  if (t === 'baselines') return loadBaselines()
  if (t === 'windows') return loadWindows()
  if (t === 'mirrors') return loadMirrors()
  return loadPkgOps()
}
async function loadRefs() {
  try { hostList.value = (await api.get('/posture/hosts')).data || [] } catch { hostList.value = [] }
  try { groups.value = ((await api.get('/host-groups')).data || []).map((g: any) => ({ id: g.id, name: g.name || g.id })) } catch { groups.value = [] }
  if (auth.isAdmin) { try { templates.value = (await api.get('/job-templates')).data || [] } catch { templates.value = [] } }
}
onMounted(() => {
  loadTab(tab.value); loadRefs()
  // "Patch" on a finding lands here with the form open for that finding only.
  if (auth.isAdmin && typeof route.query.finding === 'string' && route.query.finding) {
    openRollout({ id: route.query.finding, label: String(route.query.label || '').slice(0, 150) })
    const { finding: _f, label: _l, ...q } = route.query
    router.replace({ query: q })
  }
})

async function removeItem(path: string, id: string, name: string, what: string, reload: () => void) {
  if (!(await confirm(`Delete ${what} "${name}"?`, { title: `Delete ${what}`, confirmLabel: 'Delete', danger: true }))) return
  error.value = ''; notice.value = ''
  try { await api.delete(`/posture/${path}/${id}`); notice.value = `Deleted ${what} "${name}".`; await reload() }
  catch (e: any) { error.value = detailOf(e, `Could not delete the ${what}.`) }
}
const lines = (s: string) => [...new Set((s || '').split('\n').map((x) => x.trim()).filter(Boolean))]

// ---- New rollout ------------------------------------------------------------
const ro = ref<any>(null)
const roBusy = ref(false); const roError = ref(''); const roResult = ref<any>(null)
const pickHosts = computed(() => {
  const s = (ro.value?.hostFilter || '').trim().toLowerCase()
  return s ? hostList.value.filter((h) => h.hostname.toLowerCase().includes(s)) : hostList.value
})
function openRollout(fromFinding?: { id: string; label: string }) {
  ro.value = { finding_ids: fromFinding ? [fromFinding.id] : [] as string[], name: fromFinding?.label || '', baseline_id: '', host_ids: [] as string[], group_ids: [] as string[], window_id: '', ignore_window: false, hostFilter: '', scope: '', reboot: '', include: '', exclude: '' }
  roError.value = ''; roResult.value = null
  if (!baselines.value.length) loadBaselinesQuiet()
  if (!windows.value.length) loadWindowsQuiet()
}
async function loadBaselinesQuiet() { try { const d = (await api.get('/posture/patch-baselines')).data; baselines.value = d?.items || []; defaults.value = d?.defaults || {} } catch { /* shown empty */ } }
async function loadWindowsQuiet() { try { windows.value = (await api.get('/posture/patch-windows')).data || [] } catch { /* shown empty */ } }
const roValid = computed(() => !!ro.value && !!ro.value.name.trim() && (ro.value.finding_ids.length > 0 || ro.value.host_ids.length > 0 || ro.value.group_ids.length > 0) && (ro.value.ignore_window || !!ro.value.window_id))
async function submitRollout() {
  const f = ro.value
  if (!f || !roValid.value) return
  const config: Record<string, any> = {}
  if (f.scope) config.scope = f.scope
  if (f.reboot) config.reboot = f.reboot
  if (lines(f.include).length) config.include = lines(f.include)
  if (lines(f.exclude).length) config.exclude = lines(f.exclude)
  roBusy.value = true; roError.value = ''
  try {
    const body: Record<string, any> = { name: f.name.trim(), host_ids: f.host_ids, group_ids: f.group_ids, config, ignore_window: !!f.ignore_window }
    if (f.finding_ids.length) { body.finding_ids = f.finding_ids; body.host_ids = []; body.group_ids = []; delete config.scope }
    if (f.baseline_id) body.baseline_id = f.baseline_id
    if (!f.ignore_window) body.window_id = f.window_id
    roResult.value = (await api.post('/posture/patches', body)).data
    await loadRollouts()
  } catch (e: any) { roError.value = detailOf(e, 'Could not create the rollout.') } finally { roBusy.value = false }
}

// ---- Baselines --------------------------------------------------------------
const bl = ref<any>(null)
const blBusy = ref(false); const blError = ref('')
function openBaseline(b?: any) {
  const cfg: Record<string, any> = JSON.parse(JSON.stringify({ ...defaults.value, ...(b?.config || {}) }))
  const text: Record<string, string> = {}
  for (const k of LIST_KEYS) { text[k] = (cfg[k] || []).join('\n'); delete cfg[k] }
  bl.value = { id: b?.id || '', name: b?.name || '', description: b?.description || '', group_ids: [...(b?.group_ids || [])], cfg, text }
  blError.value = ''
}
function baselineProblem(f: any): string {
  for (const [k, [lo, hi]] of Object.entries(RANGES)) {
    const v = f.cfg[k]
    if (!Number.isInteger(v) || v < lo || v > hi) return `${NUM[k]?.l || k} must be a whole number from ${lo} to ${hi}.`
  }
  if (!!f.cfg.drain_template_id !== !!f.cfg.undrain_template_id) return 'Set both the drain and the undrain template, or neither.'
  if (f.cfg.scope === 'packages' && !lines(f.text.include).length) return 'The scope "Named packages only" needs at least one include pattern.'
  return ''
}
async function submitBaseline() {
  const f = bl.value
  if (!f) return
  const problem = baselineProblem(f)
  if (problem) { blError.value = problem; return }
  const config: Record<string, any> = { ...f.cfg, drain_template_id: f.cfg.drain_template_id || null, undrain_template_id: f.cfg.undrain_template_id || null }
  for (const k of LIST_KEYS) config[k] = lines(f.text[k])
  blBusy.value = true; blError.value = ''
  try {
    const body = { name: f.name.trim(), description: f.description, group_ids: f.group_ids, config }
    if (f.id) await api.put(`/posture/patch-baselines/${f.id}`, body); else await api.post('/posture/patch-baselines', body)
    bl.value = null; notice.value = 'Baseline saved.'; await loadBaselines()
  } catch (e: any) { blError.value = detailOf(e, 'Could not save the baseline.') } finally { blBusy.value = false }
}

// ---- Windows ----------------------------------------------------------------
const wn = ref<any>(null)
const wnBusy = ref(false); const wnError = ref('')
function openWindow(w?: any) {
  wn.value = { id: w?.id || '', name: w?.name || '', timezone: w?.timezone || 'UTC', days: [...(w?.days || [])], start: w?.start || '02:00', duration_minutes: w?.duration_minutes || 120 }
  wnError.value = ''
}
async function submitWindow() {
  const f = wn.value
  if (!f) return
  wnBusy.value = true; wnError.value = ''
  try {
    const body = { name: f.name.trim(), timezone: f.timezone.trim() || 'UTC', days: [...f.days].sort(), start: f.start, duration_minutes: f.duration_minutes }
    if (f.id) await api.put(`/posture/patch-windows/${f.id}`, body); else await api.post('/posture/patch-windows', body)
    wn.value = null; notice.value = 'Window saved.'; await loadWindows()
  } catch (e: any) { wnError.value = detailOf(e, 'Could not save the window.') } finally { wnBusy.value = false }
}

// ---- Mirrors ----------------------------------------------------------------
const mr = ref<any>(null)
const mrBusy = ref(false); const mrError = ref('')
function openMirror(m?: any) {
  mr.value = { id: m?.id || '', slug: m?.slug || '', family: m?.family || 'deb', base_url: m?.base_url || '', suite: m?.suite || '', components: m?.components || '', gpg_key: m?.gpg_key || '', group_ids: [...(m?.group_ids || [])], enabled: m ? !!m.enabled : true }
  mrError.value = ''
}
async function submitMirror() {
  const f = mr.value
  if (!f) return
  mrBusy.value = true; mrError.value = ''
  try {
    const body = { slug: f.slug.trim(), family: f.family, base_url: f.base_url.trim(), suite: f.family === 'deb' ? f.suite.trim() : '', components: f.family === 'deb' ? f.components.trim() : '', gpg_key: f.gpg_key.trim(), group_ids: f.group_ids, enabled: f.enabled }
    if (f.id) await api.put(`/posture/patch-repos/${f.id}`, body); else await api.post('/posture/patch-repos', body)
    mr.value = null; notice.value = 'Mirror saved.'; await loadMirrors()
  } catch (e: any) { mrError.value = detailOf(e, 'Could not save the mirror.') } finally { mrBusy.value = false }
}
</script>

<style scoped>
.pick { max-height: 160px; overflow: auto; padding: 8px 10px; display: flex; flex-direction: column; gap: 6px; }
.fs { border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px 14px; margin: 0; min-width: 0; }
.fs legend { padding: 0 6px; font-weight: 600; font-size: 13px; }
.nums { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
tr[tabindex]:focus-visible { outline: 2px solid var(--link); outline-offset: -2px; }
</style>
