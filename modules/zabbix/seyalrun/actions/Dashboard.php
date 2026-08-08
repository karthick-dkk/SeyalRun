<?php declare(strict_types = 1);

namespace Modules\SeyalRun\Actions;

require_once __DIR__ . '/EmbedAction.php';

class Dashboard extends EmbedAction {
	// SeyalRun's own router has the dashboard at '/' (name "dashboard"), not
	// '/dashboard' — there's no /dashboard route at all, so using that string
	// here rendered a genuinely blank page (no matching route to mount).
	protected string $route = '';
}
