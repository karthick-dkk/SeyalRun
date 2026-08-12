<?php declare(strict_types = 1);

namespace Modules\SeyalRun\Lib;

/**
 * Shown in the small footer on every SeyalRun page inside Zabbix (both the
 * iframe-embedded pages via seyalrun.embed.php and the native SSH Hosts page
 * via seyalrun.hosts.php). Bump CURRENT alongside each SeyalRun release tag —
 * also update manifest.json's own "version" field to match; Zabbix reads
 * that one for its own module listing, this one is what the page footer
 * actually renders, and the two aren't otherwise linked.
 */
final class Version {
	public const CURRENT = '1.0.2';
	public const WEBSITE_URL = 'https://seyalrun.com';
}
