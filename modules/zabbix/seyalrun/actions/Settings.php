<?php declare(strict_types = 1);

namespace Modules\SeyalRun\Actions;

require_once __DIR__ . '/EmbedAction.php';

// Administration > SeyalRun — the SeyalRun-side Settings page (rate limits, session
// timeouts, Zabbix-module trust). Gated to Zabbix super admins, matching SeyalRun's
// own superadmin-only gate on /admin/platform.
class Settings extends EmbedAction {
	protected string $route = 'admin/platform';
	protected int $min_user_type = USER_TYPE_SUPER_ADMIN;
}
