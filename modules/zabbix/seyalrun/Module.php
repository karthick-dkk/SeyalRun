<?php declare(strict_types = 1);

namespace Modules\SeyalRun;

use APP;
use CMenu;
use CMenuItem;
use CWebUser;
use Zabbix\Core\CModule as CoreModule;

/**
 * SeyalRun module entry point.
 *
 * Inserts a "SeyalRun" top-level menu item right after Monitoring (Dashboard,
 * Assets, SSH Hosts, Sessions, Automation, Trigger Bindings — job runs live
 * inside Automation's own "Recent Runs" tab, not a separate menu item; Trigger
 * Bindings only for Zabbix admins+), and, for super admins only, a "SeyalRun"
 * item under Administration for the platform-settings page.
 *
 * NOTE (read before adjusting for your Zabbix version): the menu-manipulation
 * API (`APP::Component()->get('menu.main')`, `CMenu::insertAfter()`,
 * `CMenuItem`) is stable across 7.0/8.0 in our testing, but Zabbix does shift
 * frontend internals between minor versions occasionally. If the menu doesn't
 * appear after installing, this file — specifically the two insertAfter()
 * calls below — is the one place to adjust; everything else (SSO, controllers,
 * views) is version-independent. See zabbix-module/README.md for the
 * version-shim notes and how to verify.
 */
class Module extends CoreModule {

	public function init(): void {
		$main_menu = APP::Component()->get('menu.main');

		$main_menu->insertAfter(_('Monitoring'),
			(new CMenuItem(_('SeyalRun')))
				->setId('seyalrun')
				// zi-command — Zabbix's own "run a remote command" glyph, from the
				// same icon font as every other top-level item (zi-monitoring,
				// zi-services, ...), so it renders at native size/style automatically.
				// No SeyalRun-specific icon exists in Zabbix's set; this is the
				// closest fit for an SSH/command-execution tool. See
				// include/defines.inc.php for the full ZBX_ICON_* list if you'd
				// rather pick a different one.
				->setIcon(ZBX_ICON_COMMAND)
				->setSubMenu(new CMenu([
					(new CMenuItem(_('Dashboard')))->setAction('seyalrun.dashboard'),
					(new CMenuItem(_('Assets')))->setAction('seyalrun.assets'),
					(new CMenuItem(_('SSH Hosts')))->setAction('seyalrun.hosts'),
					(new CMenuItem(_('Sessions')))->setAction('seyalrun.sessions'),
					(new CMenuItem(_('Automation')))->setAction('seyalrun.automation'),
					...(CWebUser::getType() >= USER_TYPE_ZABBIX_ADMIN
						? [(new CMenuItem(_('Trigger Bindings')))->setAction('seyalrun.bindings')]
						: [])
				]))
		);

		if (CWebUser::getType() == USER_TYPE_SUPER_ADMIN) {
			$admin_menu = $main_menu->find(_('Administration'));
			if ($admin_menu !== null && $admin_menu->getSubMenu() !== null) {
				// One flat flyout — every section directly under "SeyalRun", one hover/click
				// away. An earlier version grouped these into Access/Inventory/Automation/
				// Platform sub-flyouts (matching Administration > General's own nested
				// pattern), but that meant a second hover per group just to reach any leaf
				// item; flattening trades the grouping for getting everywhere in one step.
				$admin_menu->getSubMenu()->add(
					(new CMenuItem(_('SeyalRun')))
						->setSubMenu(new CMenu([
							(new CMenuItem(_('Users & Groups')))->setAction('seyalrun.admin.users'),
							(new CMenuItem(_('Roles')))->setAction('seyalrun.admin.roles'),
							(new CMenuItem(_('Authorizations')))->setAction('seyalrun.admin.authorizations'),
							(new CMenuItem(_('Credentials')))->setAction('seyalrun.admin.credentials'),
							(new CMenuItem(_('Zones')))->setAction('seyalrun.admin.zones'),
							(new CMenuItem(_('Integration')))->setAction('seyalrun.admin.integration'),
							(new CMenuItem(_('Trigger Bindings')))->setAction('seyalrun.bindings'),
							(new CMenuItem(_('Health')))->setAction('seyalrun.admin.health'),
							(new CMenuItem(_('Security')))->setAction('seyalrun.admin.security'),
							(new CMenuItem(_('Housekeeping')))->setAction('seyalrun.admin.housekeeping'),
							(new CMenuItem(_('Log Backend')))->setAction('seyalrun.admin.logbackend'),
							(new CMenuItem(_('Audit Logs')))->setAction('seyalrun.admin.audit'),
							(new CMenuItem(_('Settings')))->setAction('seyalrun.settings'),
						]))
				);
			}
		}
	}
}
