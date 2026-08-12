<?php declare(strict_types = 1);

namespace Modules\SeyalRun\Actions;

require_once __DIR__ . '/EmbedAction.php';

// Administration > SeyalRun > Inventory > Credentials.
class AdminCredentials extends EmbedAction {
	protected string $route = 'admin/credentials';
	protected int $min_user_type = USER_TYPE_SUPER_ADMIN;
}
