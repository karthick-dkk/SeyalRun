<?php declare(strict_types = 1);

namespace Modules\SeyalRun\Actions;

require_once __DIR__ . '/EmbedAction.php';

// Administration > SeyalRun > Access > Roles.
class AdminRoles extends EmbedAction {
	protected string $route = 'admin/roles';
	protected int $min_user_type = USER_TYPE_SUPER_ADMIN;
}
