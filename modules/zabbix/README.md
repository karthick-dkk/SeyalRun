# SeyalRun Zabbix module

Embeds SeyalRun inside Zabbix:

- A **SeyalRun** menu (right after **Monitoring**): Dashboard, Assets, SSH Hosts,
  Sessions, Jobs, Automation, and (Zabbix admins+) Trigger Bindings.
- A permission-aware **SSH Hosts** page — a terminal icon appears next to every
  Linux host the *current Zabbix user* can write to, and opens straight into
  SeyalRun's SSH terminal.
- A **SeyalRun** entry under **Administration** (super admins only) for
  SeyalRun's own settings (rate limits, session timeouts, Zabbix-module trust).

All communication is over SeyalRun's REST API — no shared database, no shared
browser storage. Trust is anchored by one HMAC secret (below).

## Requirements

- Zabbix **7.0 LTS** or **8.0**, frontend module support enabled (default since 6.4).
- A running SeyalRun deployment, reachable over HTTPS from wherever this Zabbix
  frontend's PHP runs (server-side `curl` call — doesn't need to be the same
  network the browser uses).
- PHP's `curl` extension (already required by Zabbix itself).

## Install

Verified live against Zabbix 8.0.0~alpha1 (native/apt install, nginx + PHP-FPM)
on 2026-07-11 — steps below are what actually worked, not a guess.

1. Copy this directory into Zabbix's modules folder — **inside the frontend's
   own `ui/modules/` directory**, e.g. `/usr/share/zabbix/ui/modules/` for a
   Debian/Ubuntu package install (`dpkg -L zabbix-frontend-php | grep ui$` to
   confirm the exact path on your system; for the official Docker image it's
   `/usr/share/zabbix/modules/`):

   ```sh
   sudo cp -r zabbix-module/seyalrun /usr/share/zabbix/ui/modules/seyalrun
   sudo chown -R www-data:www-data /usr/share/zabbix/ui/modules/seyalrun
   ```

2. Configure it:

   ```sh
   cd /usr/share/zabbix/ui/modules/seyalrun
   sudo cp config.php.example config.php
   ```

   Edit `config.php`:

   ```php
   return [
       'seyalrun_url' => 'https://seyalrun.example.com',        // reached by THIS SERVER
       'seyalrun_public_url' => 'https://seyalrun.example.com', // reached by the BROWSER
       'module_secret' => '<same value as ZABBIX_MODULE_SECRET below>',
       'verify_tls' => true,
   ];
   ```

   These two URLs are often the same (a real hostname reachable both ways) but
   don't have to be — on a single test box it's common for `seyalrun_url` to be
   `https://127.0.0.1:8443` (fine for this server's own curl calls) while
   `seyalrun_public_url` has to be the box's real IP/hostname, since that one
   ends up in the iframe/link URLs your browser actually loads. Getting this
   backwards is the #1 cause of a page that loads but renders blank — see
   Troubleshooting below.

3. Set the matching secret on the **SeyalRun** side — in SeyalRun's `.env`:

   ```sh
   ZABBIX_MODULE_SECRET=<openssl rand -hex 32>
   ```

   Restart `identity-service` (or the whole stack) so it picks up the new value.
   This secret is the *only* thing that lets the module assert "this Zabbix
   user, at this privilege level" to SeyalRun — treat it like a password;
   never commit `config.php` (already `.gitignore`d in this directory).

4. Allow SeyalRun to be framed by this Zabbix's origin — in SeyalRun's `.env`:

   ```sh
   FRAME_ANCESTORS=https://zabbix.example.com
   ```

   Restart `edge-proxy` to pick it up.

5. Register and enable the module. Either:

   - **UI**: **Administration → General → Modules → Scan directory**, then
     find **SeyalRun** in the list and click its status to flip it to **Enabled**.
   - **API** (handy for scripted installs — this is what was used for live
     testing): `module.create` with `relative_path` set to `modules/<dir-name>`
     (root-relative to the frontend's own directory, *not* just the module's own
     folder name — Zabbix's loader silently no-ops the module otherwise, with
     no error beyond "Cannot load module" on the Modules page):

     ```sh
     curl -s http://<zabbix>/api_jsonrpc.php -H 'Content-Type: application/json-rpc' -d '{
       "jsonrpc":"2.0","method":"user.login",
       "params":{"username":"Admin","password":"<your admin password>"},"id":1}'
     # -> use the returned token as a Bearer header below

     curl -s http://<zabbix>/api_jsonrpc.php -H 'Content-Type: application/json-rpc' \
       -H 'Authorization: Bearer <token>' -d '{
       "jsonrpc":"2.0","method":"module.create",
       "params":{"id":"seyalrun","relative_path":"modules/seyalrun","status":"1"},"id":1}'
     ```

6. Refresh Zabbix. You should see **SeyalRun** in the left-hand menu, right
   after **Monitoring**, and (as a super admin) under **Administration**.

## How the trust model works

```
Zabbix page load
  └─ PHP controller (server-side): reads the logged-in Zabbix user + type
       └─ HMAC-signs {username, zabbix_user_type} with module_secret
            └─ POST https://<seyalrun>/api/v1/auth/zbx-sso-init
                 header: X-Zabbix-Module-Signature: <hmac-sha256 hex>
       └─ SeyalRun verifies the signature, mints a 120-second one-time sso_code
  └─ page renders <iframe src="https://<seyalrun>/?sso_code=...#/<route>">
       └─ SeyalRun's frontend exchanges the code for a real session, embedded-mode UI
```

A fresh code is minted **per page load** (and per host row on the SSH Hosts
page) — codes are single-use and cheap, so every page stays self-contained
rather than depending on a shared, long-lived session.

Zabbix's own user-type maps straight onto SeyalRun's roles: Zabbix **User** →
SeyalRun `user`, **Admin** → `admin`, **Super Admin** → `superadmin`.

### SSH Hosts: visibility vs. access

The terminal icon's *visibility* is decided entirely by Zabbix's own
`host.get(editable: true)` — i.e., Zabbix's host **write** permission for the
current user. Clicking it still goes through SeyalRun's own SSO and then
SeyalRun's own PAM **authorization + credential gate** — a user with Zabbix
write access but no SeyalRun grant for that host gets a clear "request access"
message, never a shell. Zabbix permissions never bypass SeyalRun's.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Modules page shows "Cannot load module at: seyalrun" (or the module silently vanishes from the list) | `relative_path` in the `module` DB row must be `modules/<dir-name>` — root-relative to the Zabbix frontend directory — not just the module's own folder name. Delete and recreate the row (see Install step 5), or fix it via **Scan directory** instead of a raw DB/API insert. |
| A page 500s with `Call to undefined method ...::disableSIDValidation()` in the PHP log | Older Zabbix versions call this `disableSIDValidation()`; current versions renamed it `disableCsrfValidation()`. Already fixed in this module's `EmbedAction.php`/`Hosts.php` — if you see this, you're running an older copy. |
| SSH Hosts page 500s with `array_column(): ... null given` on the `groups` field | Zabbix's `host.get` omits the `groups` key entirely (not even an empty array) for a host with zero group memberships on some API versions. Already handled (`$host['groups'] ?? []`) — if you see this, you're running an older copy. |
| Menu appears, page loads (HTTP 200), but the content area is blank with a broken-page icon | `seyalrun_url` (server-side) and `seyalrun_public_url` (browser-facing) are different addresses on purpose — a valid SSO code minted server-side still renders a broken iframe if `seyalrun_public_url` isn't reachable from the *visitor's* browser (e.g. left as `127.0.0.1`, which means "the viewer's own machine" once it's in the page, not the server). Set `seyalrun_public_url` to the box's real IP/hostname. |
| Same blank/broken iframe even after `seyalrun_public_url` is correct | Self-signed TLS cert on SeyalRun's edge-proxy — browsers don't show a click-through warning *inside* an iframe, they just fail to load it silently. Open the SeyalRun URL directly in a new tab first, accept the certificate warning once, then reload the Zabbix page — the browser remembers that trust decision for the session. |
| No **SeyalRun** menu item at all | Module not Enabled under Administration → General → Modules, or the frontend needs a hard refresh/cache clear. |
| Menu item's pages show "module is not configured yet" | `config.php` missing, or `seyalrun_url`/`module_secret` blank. |
| Pages show "SeyalRun is unavailable right now" | `seyalrun_url` unreachable from the Zabbix frontend's PHP process (check with `curl` from that host/container), a TLS cert `curl` won't trust (see `verify_tls`), or `module_secret` doesn't match `ZABBIX_MODULE_SECRET` on the SeyalRun side. |
| Iframe loads but shows SeyalRun's login page instead of the embedded page | The `sso_code` exchange failed or expired (>120s between mint and load) — usually a clock-skew or a slow proxy in between; check `identity-service` logs for `sso-exchange` rejections. |
| A Zabbix SSO login succeeds but every subsequent API call 403s ("your role does not permit this action") even though the login response shows the right `role_name` | Fixed in `zabbix_sso.py` — auto-provisioned users now get a real `za_user_roles` assignment, not just the legacy `role_id` column (which SeyalRun's actual RBAC enforcement never reads). If you're on an older SeyalRun build without this fix, `roles: []` in the login response is the tell. |
| SSH Hosts page is empty | The current Zabbix user has no hosts with write permission — this is Zabbix's own permission model, not a SeyalRun setting. |
| Terminal icon opens SeyalRun but access is denied | Working as intended — the user has Zabbix write access but no SeyalRun authorization for that host. Grant it in SeyalRun's Authorizations admin page. |
| Menu doesn't appear after upgrading Zabbix minor version | `Module.php`'s two `insertAfter()` calls are the one version-sensitive spot — see the note at the top of that file. Method names inside `CController` subclasses (see the two rows above) are the other spot that's shifted between versions in practice. |

## Uninstall

Disable the module under Administration → General → Modules, then remove the
directory from Zabbix's `modules/` folder. Nothing on the SeyalRun side needs
cleanup beyond optionally clearing `ZABBIX_MODULE_SECRET`.

## What's intentionally NOT here

- **No shared database.** All communication is the REST API over HTTPS.
- **No secrets in Zabbix's own module-configuration UI.** `config.php` is a
  plain file specifically so behavior doesn't depend on Zabbix-version-specific
  module-config storage — see the comment at the top of `config.php.example`.
- **No bypass of SeyalRun's own access control.** Zabbix permissions decide
  what's *visible*; SeyalRun's authorization model still decides what's
  *reachable*, exactly as it does for every other SeyalRun login path.
