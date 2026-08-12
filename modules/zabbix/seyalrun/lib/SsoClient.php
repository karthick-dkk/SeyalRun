<?php declare(strict_types = 1);

namespace Modules\SeyalRun\Lib;

/**
 * Talks to SeyalRun's POST /api/v1/auth/zbx-sso-init endpoint to mint a
 * one-time, 120-second SSO code for the currently logged-in Zabbix user, then
 * builds the iframe/link URL SeyalRun exchanges it at.
 *
 * The request body is HMAC-signed with module_secret (config.php) — this is
 * the ONE thing that lets SeyalRun trust "this Zabbix user, at this privilege
 * level" without the user having a prior SeyalRun session. Treat module_secret
 * like SeyalRun's own ZABBIX_WEBHOOK_HMAC_SECRET: required, never logged,
 * rotated only by editing config.php on both sides.
 *
 * A fresh code is minted per page load / per host row (codes are cheap and
 * single-use) so every Zabbix page — and every "Terminal" link on the SSH
 * Hosts page — stays self-contained.
 *
 * TWO different URLs, deliberately kept separate:
 *  - $base_url   is where THIS SERVER's PHP process reaches SeyalRun (the curl
 *    call below) — on a single-host test deployment that's often 127.0.0.1,
 *    which is only meaningful from the server's own point of view.
 *  - $public_url is what gets written into the page for the VISITOR'S BROWSER
 *    to load (the iframe src / terminal link) — it must be an address the
 *    browser can actually reach, e.g. the box's real IP or a public hostname.
 * Conflating these two (pointing the browser at 127.0.0.1) is exactly why an
 * iframe can mint a valid SSO code server-side yet still render as a broken
 * page in the browser — the browser's 127.0.0.1 is the VIEWER's own machine,
 * not this server.
 */
class SsoClient {

	private string $base_url;
	private string $public_url;
	private string $secret;
	private bool $verify_tls;

	public function __construct() {
		$config_file = __DIR__ . '/../config.php';
		$config = is_file($config_file) ? require $config_file : [];

		$this->base_url = rtrim((string) ($config['seyalrun_url'] ?? ''), '/');
		// Falls back to seyalrun_url when unset, so existing configs where the
		// same address genuinely works for both (a real hostname, or Zabbix and
		// SeyalRun on different hosts entirely) keep working unchanged.
		$this->public_url = rtrim((string) ($config['seyalrun_public_url'] ?? $config['seyalrun_url'] ?? ''), '/');
		$this->secret = (string) ($config['module_secret'] ?? '');
		$this->verify_tls = (bool) ($config['verify_tls'] ?? true);
	}

	public function isConfigured(): bool {
		return $this->base_url !== '' && $this->public_url !== '' && $this->secret !== '';
	}

	/** Browser-facing base URL — safe to print into a page (iframe src, links). */
	public function baseUrl(): string {
		return $this->public_url;
	}

	/**
	 * @param string $username         Zabbix username of the currently logged-in user.
	 * @param int    $zabbix_user_type USER_TYPE_* constant (1 user / 2 admin / 3 super admin) —
	 *                                 SeyalRun maps this straight to its own user/admin/superadmin role.
	 * @return string|null             One-time sso_code, or null on ANY failure (not configured,
	 *                                 network error, SeyalRun down, HMAC rejected). Callers must
	 *                                 render a friendly "SeyalRun unavailable" state, never a blank iframe.
	 */
	public function mintSsoCode(string $username, int $zabbix_user_type): ?string {
		if (!$this->isConfigured()) {
			return null;
		}
		$body = json_encode(['username' => $username, 'zabbix_user_type' => $zabbix_user_type], JSON_UNESCAPED_SLASHES);
		if ($body === false) {
			return null;
		}
		$signature = hash_hmac('sha256', $body, $this->secret);

		$data = $this->request('POST', '/api/v1/auth/zbx-sso-init', $body, [
			'X-Zabbix-Module-Signature: ' . $signature,
		]);
		return $data !== null ? ($data['sso_code'] ?? null) : null;
	}

	/**
	 * Full URL for an embedded/linked SeyalRun route, e.g. "dashboard" or
	 * "terminal?zbx_host=123&autoconnect=1". Returns null if SSO minting
	 * failed — callers must not render an iframe/link pointing at a dead code.
	 */
	public function buildEmbedUrl(string $username, int $zabbix_user_type, string $route): ?string {
		$code = $this->mintSsoCode($username, $zabbix_user_type);
		if ($code === null) {
			return null;
		}
		return $this->public_url . '/?sso_code=' . rawurlencode($code) . '#/' . ltrim($route, '/');
	}

	/**
	 * Mints a real SeyalRun session token SERVER-SIDE (mint + exchange, both
	 * over $base_url — this token is consumed by THIS PHP process only, never
	 * handed to the browser) so a controller can make a couple of authenticated
	 * SeyalRun API calls as the current Zabbix user, e.g. to enrich the native
	 * SSH Hosts page with real SeyalRun data. Returns null on any failure —
	 * callers must degrade to Zabbix-only data, never error the whole page.
	 */
	public function mintSessionToken(string $username, int $zabbix_user_type): ?string {
		$code = $this->mintSsoCode($username, $zabbix_user_type);
		if ($code === null) {
			return null;
		}
		$data = $this->request('POST', '/api/v1/auth/sso-exchange',
			json_encode(['sso_code' => $code], JSON_UNESCAPED_SLASHES) ?: '', []);
		return $data !== null ? ($data['access_token'] ?? null) : null;
	}

	/**
	 * Authenticated GET against a SeyalRun API path (e.g. "/api/v1/hosts"),
	 * using a token from mintSessionToken(). Returns null on any failure —
	 * decode the array only when non-null.
	 */
	public function get(string $path, string $token): ?array {
		return $this->request('GET', $path, null, ['Authorization: Bearer ' . $token]);
	}

	/** Shared curl call: server-side ($base_url), decodes JSON, null on any failure. */
	private function request(string $method, string $path, ?string $body, array $extra_headers): ?array {
		if ($this->base_url === '') {
			return null;
		}
		$ch = curl_init($this->base_url . $path);
		if ($ch === false) {
			return null;
		}
		$headers = array_merge(['Content-Type: application/json'], $extra_headers);
		$opts = [
			CURLOPT_CUSTOMREQUEST => $method,
			CURLOPT_HTTPHEADER => $headers,
			CURLOPT_RETURNTRANSFER => true,
			CURLOPT_CONNECTTIMEOUT => 3,
			CURLOPT_TIMEOUT => 5,
			CURLOPT_SSL_VERIFYPEER => $this->verify_tls,
			CURLOPT_SSL_VERIFYHOST => $this->verify_tls ? 2 : 0,
		];
		if ($body !== null) {
			$opts[CURLOPT_POSTFIELDS] = $body;
		}
		curl_setopt_array($ch, $opts);
		$response = curl_exec($ch);
		$status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
		curl_close($ch);

		if ($response === false || $status !== 200) {
			return null;
		}
		$data = json_decode($response, true);
		return is_array($data) ? $data : null;
	}
}
