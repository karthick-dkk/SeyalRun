<?php declare(strict_types = 1);

namespace Modules\SeyalRun\Actions;

require_once __DIR__ . '/EmbedAction.php';

// Administration > SeyalRun > Access > Users & Groups.
class AdminUsers extends EmbedAction {
	protected string $route = 'admin/users';
	protected int $min_user_type = USER_TYPE_SUPER_ADMIN;
}
