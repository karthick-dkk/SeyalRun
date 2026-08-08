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
 * Native "SeyalRun · SSH Hosts" page — a native Zabbix page (not an iframe) that
 * shows SeyalRun's OWN host inventory, the same data as SeyalRun's Assets page:
 * name + source badge (Z synced-from-Zabbix / S SeyalRun-native), address,
 * platform, host groups, Active/Inactive status, Users count, Credentials
 * count, and a terminal icon (Linux only, matching the Assets page).
 *
 * Data source: this mints ONE server-side SeyalRun session token
 * (SsoClient::mintSessionToken — consumed only by this PHP process, never sent
 * to the browser) as the current Zabbix user, then reads SeyalRun's own
 * /hosts, /host-groups, /authorizations, /credentials — exactly the endpoints
 * AssetsView.vue reads client-side, computed here in PHP instead. The host
 * list is therefore SeyalRun's inventory, PAM-scoped to the SeyalRun user the
 * Zabbix identity maps to (the gateway filters /hosts for non-admins), NOT
 * Zabbix's raw host list.
 *
 * Core feature preserved: the terminal icon still deep-links into SeyalRun's
 * SSH terminal, which still enforces SeyalRun's own authorization + credential
 * gate — a user without a grant gets a request-access message, never a shell.
 */
class Hosts extends CController {

	protected function init(): void {
		$this->disableCsrfValidation();
	}

	protected function checkInput(): bool {
		return true;
	}

	protected function checkPermissions(): bool {
		return $this->getUserType() >= USER_TYPE_ZABBIX_USER;
	}

	protected function doAction(): void {
		$sso = new SsoClient();
		$username = (string) CWebUser::$data['username'];
		$user_type = (int) CWebUser::$data['type'];

		$rows = [];
		$reachable = false;

		if ($sso->isConfigured()) {
			$token = $sso->mintSessionToken($username, $user_type);
			if ($token !== null) {
				$hosts = $sso->get('/api/v1/hosts', $token);
				if ($hosts !== null) {
					$reachable = true;

					// group id -> name (best-effort; a plain SeyalRun 'user' role may
					// not have host-groups read — degrades to no group name, not an error).
					$group_name = [];
					foreach (($sso->get('/api/v1/host-groups', $token) ?? []) as $g) {
						$group_name[$g['id']] = $g['name'];
					}
					// Users per host — authorizations keyed by the legacy singular
					// host_id, mirroring AssetsView.vue's hostAuthzMap exactly so the
					// count matches Assets for the same host.
					$users_count = [];
					foreach (($sso->get('/api/v1/authorizations', $token) ?? []) as $a) {
						if (!empty($a['host_id'])) {
							$users_count[$a['host_id']] = ($users_count[$a['host_id']] ?? 0) + 1;
						}
					}
					// Credentials per host — host_ids array, mirroring hostCredMap.
					$creds_count = [];
					foreach (($sso->get('/api/v1/credentials', $token) ?? []) as $c) {
						foreach (($c['host_ids'] ?? []) as $hid) {
							$creds_count[$hid] = ($creds_count[$hid] ?? 0) + 1;
						}
					}

					foreach ($hosts as $h) {
						$is_windows = (($h['os_type'] ?? 'linux') === 'windows');
						$groups = [];
						foreach (($h['group_ids'] ?? []) as $gid) {
							if (isset($group_name[$gid])) {
								$groups[] = $group_name[$gid];
							}
						}
						// Terminal deep-link uses SeyalRun's own host_id (not zbx_host) —
						// Linux only, matching the Assets page's SSH-icon gate.
						$terminal_url = null;
						if (!$is_windows) {
							$terminal_url = $sso->buildEmbedUrl(
								$username, $user_type,
								'terminal?host_id=' . rawurlencode($h['id']) . '&autoconnect=1'
							);
						}
						$rows[] = [
							'name' => $h['name'],
							'address' => ($h['ip'] ?? '') . (isset($h['port']) ? ':' . $h['port'] : ''),
							'os' => $is_windows ? 'Windows' : 'Linux',
							'is_linux' => !$is_windows,
							'groups' => $groups,
							'enabled' => (bool) ($h['enabled'] ?? true),
							// Same Z/S badge as the Assets page: Z = synced from Zabbix, S = SeyalRun-native.
							'source' => !empty($h['zabbix_hostid']) ? 'Z' : 'S',
							'users_count' => $users_count[$h['id']] ?? 0,
							'creds_count' => $creds_count[$h['id']] ?? 0,
							'terminal_url' => $terminal_url,
						];
					}

					usort($rows, static fn($a, $b) => strcasecmp($a['name'], $b['name']));
				}
			}
		}

		$this->setResponse(new CControllerResponseData([
			'hosts' => $rows,
			'configured' => $sso->isConfigured(),
			'reachable' => $reachable,
			'seyalrun_url' => $sso->baseUrl(),
			'version' => Version::CURRENT,
			'website_url' => Version::WEBSITE_URL,
		]));
	}
}
