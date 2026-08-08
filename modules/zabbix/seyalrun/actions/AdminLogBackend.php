<?php declare(strict_types = 1);

namespace Modules\SeyalRun\Actions;

require_once __DIR__ . '/EmbedAction.php';

// Administration > SeyalRun > Platform > Log Backend.
class AdminLogBackend extends EmbedAction {
	protected string $route = 'admin/log-backend';
	protected int $min_user_type = USER_TYPE_SUPER_ADMIN;
}
