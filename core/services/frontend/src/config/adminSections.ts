/**
 * The one list of administrative sections.
 *
 * This used to be three hand-maintained copies — AppShell.ADMIN_GROUPS,
 * AdminView.GROUPS and SettingsView.GROUPS — and they had already drifted:
 * AppShell's tree had no Platform group at all, while AdminView's had five
 * entries in it, so the sidebar and the page disagreed about what Admin
 * contained. Seven section components were reachable at two URLs each
 * (/admin/<s> and /settings/<s>), both requiresAdmin and both gating the same
 * RBAC area, i.e. duplicate destinations rather than two permission levels.
 *
 * `shell` is what resolves that: it decides which page owns a section, and
 * therefore its canonical URL. Admin owns identity and inventory; Settings owns
 * platform configuration. Nothing can appear in both, because the path is
 * derived here rather than typed twice.
 *
 * Embedded (Zabbix iframe) mode is the reason the split cannot simply be
 * deleted: AppShell hides its sidebar AND its topbar there, so the gear icon
 * that reaches /settings does not exist and the in-page nav is the only
 * navigation. Both shells therefore render the full registry when embedded —
 * see `groupsFor()`.
 */

function _svg(body: string): string {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`
}

export const ICONS = {
  users:       _svg('<path d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z"/>'),
  shield:      _svg('<path d="M12 2.25 4.5 5.063v6.562a8.25 8.25 0 007.5 8.25 8.25 8.25 0 007.5-8.25V5.063L12 2.25z"/>'),
  shieldCheck: _svg('<path d="M12 2.25 4.5 5.063v6.562a8.25 8.25 0 007.5 8.25 8.25 8.25 0 007.5-8.25V5.063L12 2.25z"/><path d="m9 12.75 2.25 2.25 3.75-5.25"/>'),
  lock:        _svg('<path d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z"/>'),
  key:         _svg('<path d="M15.75 5.25a3 3 0 013 3m3 0a6 6 0 01-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.175.659-1.597l6.41-6.409c.404-.404.527-1 .43-1.563A6 6 0 1121.75 8.25z"/>'),
  globe:       _svg('<path d="M12 21a9.004 9.004 0 008.716-6.747M12 21a9.004 9.004 0 01-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S9.515 3 12 3m0 0a8.997 8.997 0 017.843 4.582M12 3a8.997 8.997 0 00-7.843 4.582m15.686 0A11.953 11.953 0 0112 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0121 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0112 16.5c-3.162 0-6.133-.815-8.716-2.247m0 0A9.015 9.015 0 013 12c0-1.605.42-3.113 1.157-4.418"/>'),
  link:        _svg('<path d="M13.5 10.5 21 3m0 0h-5.25M21 3v5.25M10.5 13.5 3 21m0 0h5.25M3 21v-5.25"/>'),
  gear:        _svg('<path d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065zM15 12a3 3 0 11-6 0 3 3 0 016 0z"/>'),
  pulse:       _svg('<path d="M2.25 12h3l2.25-7.5 4.5 15L14.25 9l1.5 3h6"/>'),
  trash:       _svg('<path d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0"/>'),
  archive:     _svg('<path d="M20.25 7.5l-.625 10.632a2.25 2.25 0 01-2.247 2.118H6.622a2.25 2.25 0 01-2.247-2.118L3.75 7.5M10 11.25h4M3.375 7.5h17.25c.621 0 1.125-.504 1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125z"/>'),
  list:        _svg('<path d="M8.25 6.75h12M8.25 12h12m-12 5.25h12M3.75 6.75h.007v.008H3.75V6.75zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zM3.75 12h.007v.008H3.75V12zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm-.375 5.25h.007v.008H3.75v-.008zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0z"/>'),
  bolt:        _svg('<path d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z"/>'),
  mail:        _svg('<path d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25H4.5a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75"/>'),
  chevronRight: _svg('<path d="M8.25 4.5l7.5 7.5-7.5 7.5"/>'),
  chevronLeft:  _svg('<path d="M15.75 19.5L8.25 12l7.5-7.5"/>'),
}

export type Shell = 'admin' | 'settings'

export interface Section {
  /** URL segment; the path is `/${shell}/${slug}` and is never written by hand. */
  slug: string
  /** RBAC area checked with auth.can(). Unchanged by the Admin/Settings split. */
  area: string
  label: string
  icon: string
  group: string
  shell: Shell
}

export const SECTIONS: Section[] = [
  // ── Admin: who exists, what they may reach, what they connect with ────────
  { slug: 'users',            area: 'admin.users',              label: 'Users & Groups',    icon: ICONS.users,       group: 'Access',      shell: 'admin' },
  { slug: 'roles',            area: 'admin.roles',              label: 'Roles',             icon: ICONS.shield,      group: 'Access',      shell: 'admin' },
  { slug: 'authorizations',   area: 'admin.authorizations',     label: 'Authorizations',    icon: ICONS.lock,        group: 'Access',      shell: 'admin' },
  { slug: 'credentials',      area: 'admin.credentials',        label: 'Credentials',       icon: ICONS.key,         group: 'Inventory',   shell: 'admin' },
  { slug: 'zones',            area: 'admin.zones',              label: 'Zones',             icon: ICONS.globe,       group: 'Inventory',   shell: 'admin' },
  { slug: 'trigger-bindings', area: 'admin.zabbix-integration', label: 'Trigger Bindings',  icon: ICONS.bolt,        group: 'Automation',  shell: 'admin' },

  // ── Settings: how the platform itself is configured and observed ──────────
  { slug: 'integration',      area: 'admin.integration',        label: 'Integration',       icon: ICONS.link,        group: 'Integration', shell: 'settings' },
  { slug: 'platform',         area: 'admin.platform',           label: 'SeyalRun Settings', icon: ICONS.gear,        group: 'Integration', shell: 'settings' },
  { slug: 'health',           area: 'admin.health',             label: 'Health',            icon: ICONS.pulse,       group: 'Platform',    shell: 'settings' },
  { slug: 'security',         area: 'admin.security',           label: 'Security',          icon: ICONS.shieldCheck, group: 'Platform',    shell: 'settings' },
  { slug: 'housekeeping',     area: 'admin.housekeeping',       label: 'Housekeeping',      icon: ICONS.trash,       group: 'Platform',    shell: 'settings' },
  { slug: 'log-backend',      area: 'admin.log-backend',        label: 'Log Backend',       icon: ICONS.archive,     group: 'Platform',    shell: 'settings' },
  { slug: 'mail-settings',    area: 'admin.mail-settings',      label: 'Mail Settings',     icon: ICONS.mail,        group: 'Platform',    shell: 'settings' },
  { slug: 'audit',            area: 'admin.audit',              label: 'Audit Logs',        icon: ICONS.list,        group: 'Platform',    shell: 'settings' },
]

export function pathOf(s: Section): string {
  return `/${s.shell}/${s.slug}`
}

/** Slugs that moved to Settings, so the router can redirect their old /admin URLs. */
export const MOVED_TO_SETTINGS = SECTIONS.filter(s => s.shell === 'settings').map(s => s.slug)

export interface NavGroup {
  label: string
  tabs: { area: string; to: string; label: string; icon: string }[]
}

function toGroups(sections: Section[]): NavGroup[] {
  const out: NavGroup[] = []
  for (const s of sections) {
    let g = out.find(x => x.label === s.group)
    if (!g) { g = { label: s.group, tabs: [] }; out.push(g) }
    g.tabs.push({ area: s.area, to: pathOf(s), label: s.label, icon: s.icon })
  }
  return out
}

/**
 * Nav groups for a shell. Embedded mode gets the whole registry because the
 * in-page nav is the only navigation there — an iframe user who could only see
 * their own shell's half would be unable to reach the other, and unable to get
 * back once they crossed over.
 */
export function groupsFor(shell: Shell, embedded = false): NavGroup[] {
  return toGroups(embedded ? SECTIONS : SECTIONS.filter(s => s.shell === shell))
}

/** Areas that live under Settings — drives the topbar gear's visibility. */
export const SETTINGS_AREAS = SECTIONS.filter(s => s.shell === 'settings').map(s => s.area)
