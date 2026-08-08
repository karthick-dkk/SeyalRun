<?php declare(strict_types = 1);
/**
 * @var array $data  ['hosts' => array[], 'configured' => bool, 'reachable' => bool, 'seyalrun_url' => string,
 *                     'version' => string, 'website_url' => string]
 *
 * Shows SeyalRun's OWN host inventory (same data as SeyalRun's Assets page),
 * styled to match it pixel-for-pixel — same design tokens and .badge /
 * .count-link / .ssh-icon-btn / .src-badge look as
 * services/frontend/src/assets/style.css. This is a native PHP page so it
 * can't literally share that CSS file; the tokens/classes below are copied 1:1.
 */
?>
<div class="sr-wrap">
	<div class="sr-page-header">
		<div>
			<div class="sr-page-title"><?= _('SSH Hosts') ?></div>
		</div>
	</div>

	<?php if (!$data['configured']): ?>
		<div class="sr-notice sr-notice--error">
			<?= _('The SeyalRun module is not configured yet. Copy modules/seyalrun/config.php.example to config.php and fill in seyalrun_url and module_secret.') ?>
		</div>
	<?php elseif (!$data['reachable']): ?>
		<div class="sr-notice sr-notice--error">
			<?= _('Could not reach SeyalRun to load your hosts right now. Check that SeyalRun is reachable and that ZABBIX_MODULE_SECRET matches on both sides.') ?>
		</div>
	<?php endif; ?>

	<div class="sr-card">
		<table class="sr-table">
			<thead>
				<tr>
					<th><?= _('Name') ?></th>
					<th><?= _('Address') ?></th>
					<th><?= _('Platform') ?></th>
					<th><?= _('Host Groups') ?></th>
					<th><?= _('Status') ?></th>
					<th class="sr-th-c"><?= _('Users') ?></th>
					<th class="sr-th-c"><?= _('Credentials') ?></th>
					<th class="sr-th-r"><?= _('Actions') ?></th>
				</tr>
			</thead>
			<tbody>
			<?php if (!$data['hosts']): ?>
				<tr><td colspan="8" class="sr-empty"><?= $data['reachable'] ? _('No hosts yet — add them on the SeyalRun Assets page.') : _('No hosts to show.') ?></td></tr>
			<?php else: foreach ($data['hosts'] as $host): ?>
				<tr>
					<td>
						<?php if ($host['source'] === 'Z'): ?>
							<span class="src-badge src-badge--zbx" title="<?= _('Synced from Zabbix') ?>">Z</span>
						<?php else: ?>
							<span class="src-badge src-badge--sr" title="<?= _('SeyalRun-native host') ?>">S</span>
						<?php endif; ?>
						<span class="sr-host-name"><?= htmlspecialchars($host['name'], ENT_QUOTES) ?></span>
					</td>
					<td class="sr-mono"><?= htmlspecialchars($host['address'], ENT_QUOTES) ?></td>
					<td class="sr-muted"><?= htmlspecialchars($host['os'], ENT_QUOTES) ?></td>
					<td>
						<?php if ($host['groups']): foreach ($host['groups'] as $g): ?>
							<span class="badge badge-blue"><?= htmlspecialchars($g, ENT_QUOTES) ?></span>
						<?php endforeach; else: ?>
							<span class="sr-muted">—</span>
						<?php endif; ?>
					</td>
					<td>
						<span class="sr-status <?= $host['enabled'] ? 'sr-status--ok' : '' ?>"><?= $host['enabled'] ? _('Active') : _('Inactive') ?></span>
					</td>
					<td class="sr-th-c">
						<span class="count-link<?= $host['users_count'] === 0 ? ' count-link--zero' : '' ?>">
							<span class="count-link-num"><?= (int) $host['users_count'] ?></span>
							<span class="count-link-label"><?= $host['users_count'] === 1 ? _('user') : _('users') ?></span>
						</span>
					</td>
					<td class="sr-th-c">
						<span class="count-link<?= $host['creds_count'] === 0 ? ' count-link--zero' : '' ?>">
							<span class="count-link-num"><?= (int) $host['creds_count'] ?></span>
							<span class="count-link-label"><?= $host['creds_count'] === 1 ? _('cred') : _('creds') ?></span>
						</span>
					</td>
					<td class="sr-th-r">
						<?php if ($host['terminal_url'] !== null): ?>
							<a href="<?= htmlspecialchars($host['terminal_url'], ENT_QUOTES) ?>" target="_blank" rel="noopener"
								class="ssh-icon-btn" title="<?= _('SSH into') ?> <?= htmlspecialchars($host['name'], ENT_QUOTES) ?>">
								<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6.75 7.5l3 2.25-3 2.25m4.5 0h3m-9 8.25h13.5A2.25 2.25 0 0021 18V6a2.25 2.25 0 00-2.25-2.25H5.25A2.25 2.25 0 003 6v12a2.25 2.25 0 002.25 2.25z"/></svg>
							</a>
						<?php else: ?>
							<span class="sr-muted sr-small"><?= _('not Linux') ?></span>
						<?php endif; ?>
					</td>
				</tr>
			<?php endforeach; endif; ?>
			</tbody>
		</table>
	</div>

	<div class="sr-footer">
		<a href="<?= htmlspecialchars($data['website_url'], ENT_QUOTES) ?>" target="_blank" rel="noopener">SeyalRun</a>
		<span>v<?= htmlspecialchars($data['version'], ENT_QUOTES) ?></span>
	</div>
</div>
<style>
	/* Design tokens copied 1:1 from services/frontend/src/assets/style.css so this
	   native page matches the real Assets page exactly, not just approximately. */
	/* Zabbix's own theme stylesheet (blue-theme.css / dark-theme.css etc.) sets
	   backgrounds on ancestor containers (.wrapper, .content etc.) that this native
	   PHP page renders inside — unlike the iframe-embedded pages, there's no
	   document boundary keeping Zabbix's CSS out. Every background/color below is
	   !important so this page reads the same regardless of which Zabbix theme
	   (blue/dark/high-contrast) the logged-in user has selected in their profile —
	   the same defensive pattern already used to hide Zabbix's own footer
	   (seyalrun.embed.php) earlier in this module.
	*/
	.wrapper > footer[role="contentinfo"] { display: none !important; }
	/* Same fix as seyalrun.embed.php: flex-fill the shared layout.htmlpage column
	   instead of guessing a min-height (a fixed calc(100vh - Npx) silently falls
	   short whenever the real chrome height differs by even a few px, which is
	   exactly what left a gap here after only hiding the footer). */
	.sr-wrap {
		--bg2: #0e1422; --bg3: #161d30; --border: #232c42;
		--text: #e7ecf6; --text2: #6b7690; --accent: #22c55e; --accent2: #3b82f6;
		--danger: #ef4444; --warn: #d29922; --radius: 6px;
		font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
		/* left/right gutters so content isn't flush against the Zabbix sidebar */
		font-size: 14px; color: var(--text) !important; padding: 4px 18px 10px;
		background: var(--bg2) !important;
		display: flex !important; flex-direction: column;
		flex: 1 1 auto; min-height: 0;
		margin: 0 -10px;
	}
	.sr-page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
	.sr-page-title { font-size: 22px; font-weight: 700; color: var(--text) !important; }

	.sr-card { background: var(--bg2) !important; border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
	.sr-table { width: 100%; border-collapse: collapse; font-size: 13px; background: transparent !important; }
	.sr-table th { padding: 8px 12px; text-align: left; color: var(--text2) !important; font-weight: 500; border-bottom: 1px solid var(--border); font-size: 12px; text-transform: uppercase; letter-spacing: 0.4px; background: transparent !important; }
	.sr-table td { padding: 10px 12px; border-bottom: 1px solid var(--border); color: var(--text) !important; vertical-align: middle; background: transparent !important; }
	.sr-table tr:last-child td { border-bottom: none; }
	.sr-table tr:hover td { background: var(--bg3) !important; }
	.sr-th-c { text-align: center; }
	.sr-th-r { text-align: right; }
	.sr-empty { text-align: center; padding: 32px; color: var(--text2) !important; }
	.sr-mono { font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace; font-size: 12.5px; color: var(--accent2) !important; }
	.sr-muted { color: var(--text2) !important; }
	.sr-small { font-size: 12px; }
	.sr-host-name { font-weight: 600; color: var(--text) !important; }

	.src-badge { display: inline-flex; align-items: center; justify-content: center; width: 16px; height: 16px; border-radius: 3px; font-size: 9px; font-weight: 800; line-height: 1; margin-right: 6px; }
	.src-badge--zbx { background: rgba(240,136,62,0.15) !important; border: 1px solid rgba(240,136,62,0.5); color: #f0883e !important; }
	.src-badge--sr { background: rgba(88,166,255,0.12) !important; border: 1px solid rgba(88,166,255,0.35); color: #58a6ff !important; }

	.badge { padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; display: inline-block; margin-right: 4px; }
	.badge-blue { background: rgba(59,130,246,0.18) !important; color: #60a5fa !important; }
	.badge-gray { background: rgba(107,118,144,0.2) !important; color: #8b95ae !important; }

	.sr-status { color: var(--text2) !important; font-size: 12.5px; }
	.sr-status--ok { color: #3fb950 !important; }

	.count-link { display: inline-flex; align-items: center; gap: 3px; font-size: 12px; color: #58a6ff !important; }
	.count-link-num { font-weight: 700; font-size: 13px; }
	.count-link-label { font-size: 11px; opacity: 0.85; }
	.count-link--zero { color: var(--text2) !important; }

	.ssh-icon-btn { display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--bg3) !important; color: var(--accent2) !important; cursor: pointer; transition: all 0.15s; text-decoration: none; }
	.ssh-icon-btn svg { width: 16px; height: 16px; }
	.ssh-icon-btn:hover { background: rgba(59,130,246,0.15) !important; border-color: var(--accent2); }

	.sr-notice { max-width: 900px; padding: 12px 14px; margin-bottom: 14px; border-radius: var(--radius); font-size: 13px; line-height: 1.6; }
	.sr-notice--error { background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.35); color: #f87171; }

	/* Pinned to the bottom of .sr-wrap the same way Zabbix's own (now-hidden)
	 * footer pinned itself — margin-top: auto inside the flex column. */
	.sr-footer { margin-top: auto; display: flex; align-items: center; justify-content: center; gap: 14px; padding: 10px 0 4px; font-size: 11px; color: var(--text2) !important; }
	.sr-footer a { color: var(--accent2) !important; text-decoration: none; }
	.sr-footer a:hover { text-decoration: underline; }
</style>
