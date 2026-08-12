<?php declare(strict_types = 1);

namespace Modules\SeyalRun\Actions;

require_once __DIR__ . '/EmbedAction.php';

class Bindings extends EmbedAction {
	// Its own dedicated admin page now: TriggerBindingsAdmin.vue at
	// '/admin/trigger-bindings' (split out from IntegrationAdmin.vue, which
	// used to embed the bindings table inline under general Zabbix
	// integration settings).
	// SeyalRun's own router gates this route to admin/superadmin, so a plain Zabbix
	// user landing here via SSO would just hit a redirect inside the iframe — gate
	// the menu item itself to Zabbix admins so that never happens.
	protected string $route = 'admin/trigger-bindings';
	protected int $min_user_type = USER_TYPE_ZABBIX_ADMIN;
}
