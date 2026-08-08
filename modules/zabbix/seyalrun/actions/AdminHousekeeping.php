<?php declare(strict_types = 1);

namespace Modules\SeyalRun\Actions;

require_once __DIR__ . '/EmbedAction.php';

// Administration > SeyalRun > Platform > Housekeeping.
class AdminHousekeeping extends EmbedAction {
	protected string $route = 'admin/housekeeping';
	protected int $min_user_type = USER_TYPE_SUPER_ADMIN;
}
