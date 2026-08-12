<?php declare(strict_types = 1);

namespace Modules\SeyalRun\Actions;

require_once __DIR__ . '/EmbedAction.php';

class Sessions extends EmbedAction {
	protected string $route = 'sessions';
}
