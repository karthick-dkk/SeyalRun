<?php declare(strict_types = 1);
/**
 * @var array $data  ['iframe_url' => string|null, 'seyalrun_url' => string, 'configured' => bool,
 *                     'version' => string, 'website_url' => string]
 */

if (!$data['configured']) {
	echo '<div class="seyalrun-embed-wrap seyalrun-embed-wrap--error">'
		. '<div class="seyalrun-error">'
		. _('The SeyalRun module is not configured yet. Copy modules/seyalrun/config.php.example to config.php and fill in seyalrun_url and module_secret.')
		. '</div></div>';
} elseif ($data['iframe_url'] === null) {
	echo '<div class="seyalrun-embed-wrap seyalrun-embed-wrap--error">'
		. '<div class="seyalrun-error">'
		. _('SeyalRun is unavailable right now (could not sign in). Check that SeyalRun is reachable at ')
		. htmlspecialchars($data['seyalrun_url'], ENT_QUOTES)
		. _(' and that ZABBIX_MODULE_SECRET matches on both sides.')
		. '</div></div>';
} else {
	echo '<div class="seyalrun-embed-wrap">'
		. '<iframe src="' . htmlspecialchars($data['iframe_url'], ENT_QUOTES)
		. '" class="seyalrun-iframe" allow="clipboard-read; clipboard-write"></iframe>'
		. '</div>';
}

echo '<div class="seyalrun-footer">'
	. '<a href="' . htmlspecialchars($data['website_url'], ENT_QUOTES) . '" target="_blank" rel="noopener">SeyalRun</a>'
	. '<span>v' . htmlspecialchars($data['version'], ENT_QUOTES) . '</span>'
	. '</div>';
?>
<style>
	/* Native full-height feel — no Zabbix footer, no dark band.
	 *
	 * Zabbix's main column `.wrapper` is `display:flex; flex-direction:column`
	 * filling the viewport, and its own `<footer>` uses `margin-top:auto` to
	 * pin itself to the bottom — the gap that margin-top:auto opens ABOVE the
	 * footer is the dark empty band we kept seeing below a short iframe.
	 *
	 * Fix, entirely on the Zabbix side (this <style> only loads on SeyalRun
	 * pages, so it never affects the rest of Zabbix):
	 *   1. Hide that page footer — removes it from the flex flow entirely.
	 *   2. Make our iframe wrapper flex-fill the column, so it takes the whole
	 *      viewport height with NO pixel-guessing (works at any window size).
	 * SeyalRun's own app fills the iframe with its own dark background and
	 * scrolls internally (its `.page` is `overflow-y:auto`), so you only ever
	 * see SeyalRun — never Zabbix's wrapper background or footer. */
	.wrapper > footer[role="contentinfo"] { display: none !important; }

	/* min-height (not flex-basis) guarantees the iframe is always at least a
	 * full viewport tall — it no longer shrinks to make room for the footer
	 * sibling below. Short pages: the footer sits right after this, visible
	 * with no scroll. Tall pages: same as Zabbix's own footer pattern — the
	 * page grows past one viewport and the footer only appears once you
	 * scroll down to it, exactly like Zabbix's native "Zabbix X.Y.Z ©
	 * ... Zabbix SIA" footer bar (which we hide below and replace the
	 * content of, in this same spot, in SeyalRun's own style). */
	.seyalrun-embed-wrap { flex: 1 1 auto; min-height: 100vh; display: flex; width: 100%; }
	.seyalrun-iframe { flex: 1 1 auto; width: 100%; min-height: 400px; border: 0; }

	.seyalrun-embed-wrap--error { display: flex; align-items: flex-start; justify-content: center; padding-top: 60px; }
	.seyalrun-error { max-width: 560px; padding: 16px 18px; border-radius: 4px; background: #fef6f6; border: 1px solid #e45959; color: #7a2020; font-size: 13px; line-height: 1.6; }

	/* Same footer bar Zabbix itself uses (small muted text, right-aligned,
	 * underlined link) — SeyalRun branding instead of Zabbix's. It sits
	 * OUTSIDE the iframe on Zabbix's own page background (light, in
	 * Zabbix's default theme), so it needs its own explicit dark background
	 * to read as part of the app rather than a mismatched Zabbix element. */
	.seyalrun-footer { flex: 0 0 auto; margin-top: auto; display: flex; align-items: center; justify-content: flex-end; gap: 10px; padding: 10px 20px; font-size: 11px; color: #8b95ae; background: #0a0e1a !important; }
	.seyalrun-footer a { color: #8b95ae; text-decoration: underline; }
	.seyalrun-footer a:hover { color: #58a6ff; }
</style>
