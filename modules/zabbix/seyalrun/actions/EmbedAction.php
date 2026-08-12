<?php declare(strict_types = 1);

namespace Modules\SeyalRun\Actions;

require_once __DIR__ . '/../lib/SsoClient.php';
require_once __DIR__ . '/../lib/Version.php';

use CController;
use CControllerResponseData;
use CWebUser;
use Modules\SeyalRun\Lib\SsoClient;
use Modules\SeyalRun\Lib\Version;

/**
 * Shared base for every "embedded SeyalRun page" action (Dashboard, Assets,
 * Sessions, Jobs, Automation, Trigger Bindings, Settings). Each subclass sets
 * $route to the SeyalRun hash-route it should land on; this mints a fresh,
 * single-use SSO code per page load (see SsoClient — codes are cheap) so every
 * Zabbix page stays self-contained rather than sharing one long-lived session.
 */
abstract class EmbedAction extends CController {

	/** @var string SeyalRun hash-route this page embeds, e.g. "dashboard". */
	protected string $route = '';

	/** Minimum Zabbix user type required to open this page. */
	protected int $min_user_type = USER_TYPE_ZABBIX_USER;

	protected function init(): void {
		// GET-only, no state-changing side effects (each page load just mints a
		// fresh SSO code) — CSRF validation exists for state-changing requests,
		// not applicable here. Method name confirmed against this Zabbix
		// build's actual include/classes/mvc/CController.php (it varies by
		// version — this is the current one; SID-based naming is a 6.x term).
		$this->disableCsrfValidation();
	}

	protected function checkInput(): bool {
		return true;
	}

	protected function checkPermissions(): bool {
		return $this->getUserType() >= $this->min_user_type;
	}

	protected function doAction(): void {
		$sso = new SsoClient();
		$iframe_url = $sso->isConfigured()
			? $sso->buildEmbedUrl((string) CWebUser::$data['username'], (int) CWebUser::$data['type'], $this->route)
			: null;

		$this->setResponse(new CControllerResponseData([
			'iframe_url' => $iframe_url,
			'seyalrun_url' => $sso->baseUrl(),
			'configured' => $sso->isConfigured(),
			'version' => Version::CURRENT,
			'website_url' => Version::WEBSITE_URL,
		]));
	}
}
