<?php declare(strict_types = 1);

namespace Modules\SeyalRun\Actions;

require_once __DIR__ . '/EmbedAction.php';

// Administration > SeyalRun > Automation > Integration.
class AdminIntegration extends EmbedAction {
	protected string $route = 'admin/integration';
	protected int $min_user_type = USER_TYPE_SUPER_ADMIN;
}
