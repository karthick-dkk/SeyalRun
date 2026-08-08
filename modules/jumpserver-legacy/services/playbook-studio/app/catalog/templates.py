"""Pre-built Ansible playbook template library — 20 templates.

Templates are static code — never stored in DB.
Cloning creates a new Playbook row with source_template_id set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TemplateVar:
    name: str
    type: str = "str"
    description: str = ""
    required: bool = False
    default: Any = None


@dataclass
class Template:
    slug: str
    name: str
    description: str
    category: str
    tags: list[str]
    required_vars: list[TemplateVar]
    tasks: list[dict]
    estimated_duration_seconds: int
    risk_level: str  # low / medium / high


TEMPLATES: list[Template] = [
    # ── System (6) ───────────────────────────────────────────────────────
    Template(
        slug="patch-management-ubuntu",
        name="Ubuntu Security Patching",
        description="Apply security updates on Ubuntu/Debian hosts with optional reboot",
        category="system",
        tags=["patch", "ubuntu", "security", "apt"],
        required_vars=[
            TemplateVar("reboot_if_needed", "bool", "Reboot if kernel/libs updated", default=False),
            TemplateVar("reboot_timeout", "int", "Seconds to wait for reboot", default=300),
        ],
        tasks=[
            {
                "task_id": "t1",
                "name": "Update apt cache",
                "module": "ansible.builtin.apt",
                "params": {"update_cache": True, "cache_valid_time": 0},
            },
            {
                "task_id": "t2",
                "name": "Apply security updates",
                "module": "ansible.builtin.apt",
                "params": {"upgrade": "safe", "only_upgrade": True},
                "register": "apt_result",
            },
            {
                "task_id": "t3",
                "name": "Check if reboot required",
                "module": "ansible.builtin.stat",
                "params": {"path": "/var/run/reboot-required"},
                "register": "reboot_required",
            },
            {
                "task_id": "t4",
                "name": "Reboot if required",
                "module": "ansible.builtin.reboot",
                "params": {
                    "reboot_timeout": "{{ reboot_timeout }}",
                    "msg": "Rebooting after security updates",
                },
                "when": "reboot_required.stat.exists and reboot_if_needed | bool",
            },
        ],
        estimated_duration_seconds=120,
        risk_level="medium",
    ),
    Template(
        slug="patch-management-rhel",
        name="RHEL/CentOS Security Patching",
        description="Apply security updates on RHEL/CentOS/Fedora hosts with optional reboot",
        category="system",
        tags=["patch", "rhel", "centos", "security", "yum", "dnf"],
        required_vars=[
            TemplateVar("reboot_if_needed", "bool", "Reboot after update", default=False),
            TemplateVar("reboot_timeout", "int", "Seconds to wait for reboot", default=300),
        ],
        tasks=[
            {
                "task_id": "t1",
                "name": "Apply security updates (yum)",
                "module": "ansible.builtin.yum",
                "params": {"name": "*", "state": "latest", "security": True},
                "register": "yum_result",
                "when": "ansible_pkg_mgr == 'yum'",
            },
            {
                "task_id": "t2",
                "name": "Apply security updates (dnf)",
                "module": "ansible.builtin.dnf",
                "params": {"name": "*", "state": "latest", "security": True},
                "register": "dnf_result",
                "when": "ansible_pkg_mgr == 'dnf'",
            },
            {
                "task_id": "t3",
                "name": "Reboot if required",
                "module": "ansible.builtin.reboot",
                "params": {"reboot_timeout": "{{ reboot_timeout }}"},
                "when": "(yum_result.changed or dnf_result.changed) and reboot_if_needed | bool",
            },
        ],
        estimated_duration_seconds=180,
        risk_level="medium",
    ),
    Template(
        slug="user-management-create",
        name="Create Linux User with SSH Key",
        description="Create a user account, assign groups, and push SSH public key",
        category="system",
        tags=["users", "ssh", "access", "onboarding"],
        required_vars=[
            TemplateVar("username", required=True, description="Linux username to create"),
            TemplateVar("user_groups", "list", "Supplementary groups", default=["sudo"]),
            TemplateVar("ssh_public_key", required=True, description="SSH public key string"),
            TemplateVar("user_comment", "str", "Full name / GECOS comment", default=""),
            TemplateVar("user_shell", "str", "Login shell", default="/bin/bash"),
        ],
        tasks=[
            {
                "task_id": "t1",
                "name": "Create user account",
                "module": "ansible.builtin.user",
                "params": {
                    "name": "{{ username }}",
                    "groups": "{{ user_groups }}",
                    "append": True,
                    "shell": "{{ user_shell }}",
                    "comment": "{{ user_comment }}",
                    "create_home": True,
                },
            },
            {
                "task_id": "t2",
                "name": "Add SSH public key",
                "module": "ansible.posix.authorized_key",
                "params": {
                    "user": "{{ username }}",
                    "key": "{{ ssh_public_key }}",
                    "state": "present",
                },
            },
            {
                "task_id": "t3",
                "name": "Verify user exists",
                "module": "ansible.builtin.command",
                "params": {"cmd": "id {{ username }}"},
                "register": "user_check",
            },
            {
                "task_id": "t4",
                "name": "Print user info",
                "module": "ansible.builtin.debug",
                "params": {"msg": "Created user {{ username }}: {{ user_check.stdout }}"},
            },
        ],
        estimated_duration_seconds=15,
        risk_level="low",
    ),
    Template(
        slug="user-management-offboard",
        name="Offboard Linux User",
        description="Lock user account, terminate active sessions, and archive home directory",
        category="system",
        tags=["users", "offboarding", "security", "deprovisioning"],
        required_vars=[
            TemplateVar("username", required=True, description="Username to offboard"),
            TemplateVar("archive_home", "bool", "Archive home dir before removal", default=True),
        ],
        tasks=[
            {
                "task_id": "t1",
                "name": "Kill all user sessions",
                "module": "ansible.builtin.command",
                "params": {"cmd": "pkill -KILL -u {{ username }}"},
                "ignore_errors": True,
            },
            {
                "task_id": "t2",
                "name": "Lock the account",
                "module": "ansible.builtin.user",
                "params": {"name": "{{ username }}", "password_lock": True},
            },
            {
                "task_id": "t3",
                "name": "Archive home directory",
                "module": "ansible.builtin.command",
                "params": {
                    "cmd": "tar -czf /var/archive/{{ username }}-{{ ansible_date_time.date }}.tar.gz /home/{{ username }}"
                },
                "when": "archive_home | bool",
                "become": True,
            },
            {
                "task_id": "t4",
                "name": "Remove from sudoers",
                "module": "ansible.builtin.file",
                "params": {"path": "/etc/sudoers.d/{{ username }}", "state": "absent"},
            },
        ],
        estimated_duration_seconds=30,
        risk_level="medium",
    ),
    Template(
        slug="disk-cleanup",
        name="Disk Space Cleanup",
        description="Free disk space by cleaning old logs, apt/yum caches, and temp files",
        category="system",
        tags=["disk", "cleanup", "maintenance", "logs"],
        required_vars=[
            TemplateVar("log_retention_days", "int", "Delete logs older than N days", default=30),
            TemplateVar("tmp_cleanup", "bool", "Clean /tmp directory", default=True),
        ],
        tasks=[
            {
                "task_id": "t1",
                "name": "Report disk usage before",
                "module": "ansible.builtin.command",
                "params": {"cmd": "df -h /"},
                "register": "disk_before",
            },
            {
                "task_id": "t2",
                "name": "Remove old log files",
                "module": "ansible.builtin.find",
                "params": {
                    "paths": ["/var/log"],
                    "age": "{{ log_retention_days }}d",
                    "recurse": True,
                    "file_type": "file",
                },
                "register": "old_logs",
            },
            {
                "task_id": "t3",
                "name": "Delete found old logs",
                "module": "ansible.builtin.file",
                "params": {"path": "{{ item.path }}", "state": "absent"},
                "loop": "{{ old_logs.files }}",
                "when": "old_logs.files | length > 0",
            },
            {
                "task_id": "t4",
                "name": "Vacuum journald logs",
                "module": "ansible.builtin.command",
                "params": {"cmd": "journalctl --vacuum-time={{ log_retention_days }}d"},
                "become": True,
            },
            {
                "task_id": "t5",
                "name": "Clean apt cache",
                "module": "ansible.builtin.apt",
                "params": {"autoclean": True, "autoremove": True},
                "when": "ansible_pkg_mgr == 'apt'",
            },
            {
                "task_id": "t6",
                "name": "Report disk usage after",
                "module": "ansible.builtin.command",
                "params": {"cmd": "df -h /"},
                "register": "disk_after",
            },
            {
                "task_id": "t7",
                "name": "Show space freed",
                "module": "ansible.builtin.debug",
                "params": {
                    "msg": "Before: {{ disk_before.stdout_lines[1] }} | After: {{ disk_after.stdout_lines[1] }}"
                },
            },
        ],
        estimated_duration_seconds=60,
        risk_level="low",
    ),
    Template(
        slug="gather-system-facts",
        name="Collect System Inventory Facts",
        description="Gather and display comprehensive system information: OS, hardware, packages, services",
        category="system",
        tags=["facts", "inventory", "audit", "discovery"],
        required_vars=[],
        tasks=[
            {
                "task_id": "t1",
                "name": "Gather all facts",
                "module": "ansible.builtin.setup",
                "params": {"gather_subset": ["all"]},
            },
            {
                "task_id": "t2",
                "name": "Get installed packages count",
                "module": "ansible.builtin.shell",
                "params": {
                    "cmd": "dpkg -l 2>/dev/null | grep -c '^ii' || rpm -qa 2>/dev/null | wc -l || echo 0"
                },
                "register": "pkg_count",
                "ignore_errors": True,
            },
            {
                "task_id": "t3",
                "name": "Get running services count",
                "module": "ansible.builtin.shell",
                "params": {
                    "cmd": "systemctl list-units --state=running --no-pager 2>/dev/null | grep -c '\\.service' || echo 0"
                },
                "register": "svc_count",
                "ignore_errors": True,
            },
            {
                "task_id": "t4",
                "name": "Display system summary",
                "module": "ansible.builtin.debug",
                "params": {
                    "msg": [
                        "Host: {{ ansible_facts['hostname'] }}",
                        "OS: {{ ansible_facts['distribution'] }} {{ ansible_facts['distribution_version'] }}",
                        "Arch: {{ ansible_facts['architecture'] }}",
                        "CPU: {{ ansible_facts['processor_cores'] }} cores",
                        "RAM: {{ (ansible_facts['memtotal_mb'] / 1024) | round(1) }} GB",
                        "Packages: {{ pkg_count.stdout | default('?') | trim }}",
                        "Services: {{ svc_count.stdout | default('?') | trim }}",
                    ]
                },
            },
        ],
        estimated_duration_seconds=20,
        risk_level="low",
    ),
    # ── Security (5) ─────────────────────────────────────────────────────
    Template(
        slug="ssh-hardening",
        name="SSH Configuration Hardening",
        description="Apply CIS-aligned SSHD security baseline (no password auth, key-only, rate limits)",
        category="security",
        tags=["ssh", "hardening", "cis", "security", "sshd"],
        required_vars=[
            TemplateVar("allowed_ssh_users", "list", "Users allowed to SSH", default=[]),
            TemplateVar("ssh_port", "int", "SSH port number", default=22),
            TemplateVar("max_auth_tries", "int", "Max authentication attempts", default=3),
        ],
        tasks=[
            {
                "task_id": "t1",
                "name": "Disable password authentication",
                "module": "ansible.builtin.lineinfile",
                "params": {
                    "path": "/etc/ssh/sshd_config",
                    "regexp": "^#?PasswordAuthentication",
                    "line": "PasswordAuthentication no",
                    "validate": "sshd -t -f %s",
                },
            },
            {
                "task_id": "t2",
                "name": "Disable root login",
                "module": "ansible.builtin.lineinfile",
                "params": {
                    "path": "/etc/ssh/sshd_config",
                    "regexp": "^#?PermitRootLogin",
                    "line": "PermitRootLogin no",
                    "validate": "sshd -t -f %s",
                },
            },
            {
                "task_id": "t3",
                "name": "Set max auth tries",
                "module": "ansible.builtin.lineinfile",
                "params": {
                    "path": "/etc/ssh/sshd_config",
                    "regexp": "^#?MaxAuthTries",
                    "line": "MaxAuthTries {{ max_auth_tries }}",
                    "validate": "sshd -t -f %s",
                },
            },
            {
                "task_id": "t4",
                "name": "Disable X11 forwarding",
                "module": "ansible.builtin.lineinfile",
                "params": {
                    "path": "/etc/ssh/sshd_config",
                    "regexp": "^#?X11Forwarding",
                    "line": "X11Forwarding no",
                    "validate": "sshd -t -f %s",
                },
            },
            {
                "task_id": "t5",
                "name": "Validate SSHD config",
                "module": "ansible.builtin.command",
                "params": {"cmd": "sshd -t"},
            },
            {
                "task_id": "t6",
                "name": "Reload SSHD",
                "module": "ansible.builtin.service",
                "params": {"name": "sshd", "state": "reloaded"},
            },
        ],
        estimated_duration_seconds=20,
        risk_level="medium",
    ),
    Template(
        slug="ssl-cert-check",
        name="SSL Certificate Expiry Audit",
        description="Check SSL certificate expiry dates and alert if expiring within threshold",
        category="security",
        tags=["ssl", "tls", "certificates", "audit", "expiry"],
        required_vars=[
            TemplateVar("cert_paths", "list", "Paths to certificate files", required=True),
            TemplateVar("warn_days", "int", "Days before expiry to warn", default=30),
        ],
        tasks=[
            {
                "task_id": "t1",
                "name": "Check certificate expiry",
                "module": "ansible.builtin.command",
                "params": {"cmd": "openssl x509 -in {{ item }} -noout -enddate"},
                "register": "cert_expiry",
                "loop": "{{ cert_paths }}",
            },
            {
                "task_id": "t2",
                "name": "Get days until expiry",
                "module": "ansible.builtin.command",
                "params": {
                    "cmd": "openssl x509 -in {{ item }} -noout -checkend {{ (warn_days * 86400) | int }}"
                },
                "register": "cert_check",
                "loop": "{{ cert_paths }}",
                "ignore_errors": True,
            },
            {
                "task_id": "t3",
                "name": "Report certificate status",
                "module": "ansible.builtin.debug",
                "params": {
                    "msg": "Cert {{ item.item }}: {{ 'EXPIRING SOON or EXPIRED' if item.rc != 0 else 'OK' }}"
                },
                "loop": "{{ cert_check.results }}",
            },
            {
                "task_id": "t4",
                "name": "Fail if any cert is expiring",
                "module": "ansible.builtin.fail",
                "params": {
                    "msg": "Certificate {{ item.item }} is expiring within {{ warn_days }} days!"
                },
                "when": "item.rc != 0",
                "loop": "{{ cert_check.results }}",
            },
        ],
        estimated_duration_seconds=15,
        risk_level="low",
    ),
    Template(
        slug="firewall-baseline-ufw",
        name="UFW Firewall Baseline",
        description="Apply UFW firewall baseline: deny all incoming, allow outgoing, open specific ports",
        category="security",
        tags=["firewall", "ufw", "security", "hardening", "network"],
        required_vars=[
            TemplateVar(
                "allowed_ports",
                "list",
                "Ports to allow [{port, proto, comment}]",
                default=[{"port": "22", "proto": "tcp", "comment": "SSH"}],
            ),
            TemplateVar("ssh_port", "int", "SSH port (ensure it is in allowed_ports!)", default=22),
        ],
        tasks=[
            {
                "task_id": "t1",
                "name": "Install UFW",
                "module": "ansible.builtin.apt",
                "params": {"name": ["ufw"], "state": "present"},
                "when": "ansible_pkg_mgr == 'apt'",
            },
            {
                "task_id": "t2",
                "name": "Set default incoming to deny",
                "module": "community.general.ufw",
                "params": {"direction": "incoming", "policy": "deny"},
            },
            {
                "task_id": "t3",
                "name": "Set default outgoing to allow",
                "module": "community.general.ufw",
                "params": {"direction": "outgoing", "policy": "allow"},
            },
            {
                "task_id": "t4",
                "name": "Allow configured ports",
                "module": "community.general.ufw",
                "params": {
                    "rule": "allow",
                    "port": "{{ item.port }}",
                    "proto": "{{ item.proto }}",
                    "comment": "{{ item.comment | default('') }}",
                },
                "loop": "{{ allowed_ports }}",
            },
            {
                "task_id": "t5",
                "name": "Enable UFW",
                "module": "community.general.ufw",
                "params": {"state": "enabled"},
            },
        ],
        estimated_duration_seconds=30,
        risk_level="high",
    ),
    Template(
        slug="audit-sshd-config",
        name="SSHD Configuration Audit",
        description="Read-only audit of SSHD configuration against security baseline (no changes made)",
        category="security",
        tags=["audit", "ssh", "compliance", "read-only"],
        required_vars=[],
        tasks=[
            {
                "task_id": "t1",
                "name": "Read sshd_config",
                "module": "ansible.builtin.command",
                "params": {"cmd": "sshd -T"},
                "register": "sshd_config",
            },
            {
                "task_id": "t2",
                "name": "Check PasswordAuthentication",
                "module": "ansible.builtin.debug",
                "params": {
                    "msg": "PasswordAuthentication: {{ (sshd_config.stdout | regex_search('passwordauthentication (\\w+)', '\\1'))[0] }}"
                },
            },
            {
                "task_id": "t3",
                "name": "Check PermitRootLogin",
                "module": "ansible.builtin.debug",
                "params": {
                    "msg": "PermitRootLogin: {{ (sshd_config.stdout | regex_search('permitrootlogin (\\w+)', '\\1'))[0] }}"
                },
            },
            {
                "task_id": "t4",
                "name": "Fail if password auth is enabled",
                "module": "ansible.builtin.fail",
                "params": {"msg": "AUDIT FAIL: PasswordAuthentication is not 'no'"},
                "when": "'passwordauthentication no' not in sshd_config.stdout",
            },
        ],
        estimated_duration_seconds=10,
        risk_level="low",
    ),
    Template(
        slug="rotate-sudo-access",
        name="Rotate Sudo Access",
        description="Remove and re-grant sudo access for a user with updated permissions",
        category="security",
        tags=["sudo", "access", "rotation", "security", "privilege"],
        required_vars=[
            TemplateVar("username", required=True, description="Target username"),
            TemplateVar(
                "sudo_spec",
                required=True,
                description="Sudo rule (e.g. ALL=(ALL) NOPASSWD: /bin/systemctl)",
            ),
        ],
        tasks=[
            {
                "task_id": "t1",
                "name": "Remove existing sudoers entry",
                "module": "ansible.builtin.file",
                "params": {"path": "/etc/sudoers.d/{{ username }}", "state": "absent"},
            },
            {
                "task_id": "t2",
                "name": "Add new sudoers entry",
                "module": "ansible.builtin.lineinfile",
                "params": {
                    "path": "/etc/sudoers.d/{{ username }}",
                    "line": "{{ username }} {{ sudo_spec }}",
                    "create": True,
                    "mode": "0440",
                    "validate": "visudo -cf %s",
                },
            },
            {
                "task_id": "t3",
                "name": "Verify sudoers file is valid",
                "module": "ansible.builtin.command",
                "params": {"cmd": "visudo -c"},
            },
        ],
        estimated_duration_seconds=10,
        risk_level="medium",
    ),
    # ── Networking (3) ───────────────────────────────────────────────────
    Template(
        slug="check-port-connectivity",
        name="Port Connectivity Matrix Check",
        description="Validate that specific ports are reachable from managed hosts",
        category="networking",
        tags=["network", "connectivity", "audit", "ports"],
        required_vars=[
            TemplateVar(
                "target_hosts_list", "list", "Hosts to check [{host, ports}]", required=True
            ),
        ],
        tasks=[
            {
                "task_id": "t1",
                "name": "Check port connectivity",
                "module": "ansible.builtin.wait_for",
                "params": {
                    "host": "{{ item.host }}",
                    "port": "{{ item.port }}",
                    "timeout": 10,
                    "state": "started",
                },
                "loop": "{{ target_hosts_list }}",
                "register": "port_check",
                "ignore_errors": True,
            },
            {
                "task_id": "t2",
                "name": "Report connectivity results",
                "module": "ansible.builtin.debug",
                "params": {
                    "msg": "{{ item.item.host }}:{{ item.item.port }} - {{ 'OPEN' if not item.failed else 'CLOSED/FILTERED' }}"
                },
                "loop": "{{ port_check.results }}",
            },
        ],
        estimated_duration_seconds=30,
        risk_level="low",
    ),
    Template(
        slug="configure-ntp",
        name="Configure NTP/Chrony Time Sync",
        description="Install and configure chrony for accurate time synchronization",
        category="networking",
        tags=["ntp", "time", "chrony", "configuration"],
        required_vars=[
            TemplateVar(
                "ntp_servers",
                "list",
                "NTP server addresses",
                default=["0.pool.ntp.org", "1.pool.ntp.org"],
            ),
            TemplateVar("timezone", "str", "System timezone", default="UTC"),
        ],
        tasks=[
            {
                "task_id": "t1",
                "name": "Install chrony",
                "module": "ansible.builtin.package",
                "params": {"name": "chrony", "state": "present"},
            },
            {
                "task_id": "t2",
                "name": "Configure chrony servers",
                "module": "ansible.builtin.template",
                "params": {
                    "src": "templates/chrony.conf.j2",
                    "dest": "/etc/chrony.conf",
                    "mode": "0644",
                },
                "ignore_errors": True,
            },
            {
                "task_id": "t3",
                "name": "Set timezone",
                "module": "community.general.timezone",
                "params": {"name": "{{ timezone }}"},
            },
            {
                "task_id": "t4",
                "name": "Enable and start chrony",
                "module": "ansible.builtin.service",
                "params": {"name": "chronyd", "state": "started", "enabled": True},
            },
            {
                "task_id": "t5",
                "name": "Verify time sync",
                "module": "ansible.builtin.command",
                "params": {"cmd": "chronyc tracking"},
                "register": "chrony_status",
            },
        ],
        estimated_duration_seconds=30,
        risk_level="low",
    ),
    Template(
        slug="dns-resolution-audit",
        name="DNS Resolution Audit",
        description="Verify DNS resolution is working correctly for a list of domains",
        category="networking",
        tags=["dns", "audit", "networking", "resolution"],
        required_vars=[
            TemplateVar("domains_to_check", "list", "Domains to resolve", required=True),
            TemplateVar("expected_resolver", "str", "Expected DNS resolver IP (optional)"),
        ],
        tasks=[
            {
                "task_id": "t1",
                "name": "Check current DNS resolver",
                "module": "ansible.builtin.command",
                "params": {"cmd": "cat /etc/resolv.conf"},
                "register": "resolv_conf",
            },
            {
                "task_id": "t2",
                "name": "Resolve each domain",
                "module": "ansible.builtin.command",
                "params": {"cmd": "dig +short {{ item }}"},
                "loop": "{{ domains_to_check }}",
                "register": "dns_results",
            },
            {
                "task_id": "t3",
                "name": "Report DNS results",
                "module": "ansible.builtin.debug",
                "params": {
                    "msg": "{{ item.item }}: {{ item.stdout if item.stdout else 'UNRESOLVED' }}"
                },
                "loop": "{{ dns_results.results }}",
            },
            {
                "task_id": "t4",
                "name": "Fail if any domain unresolved",
                "module": "ansible.builtin.fail",
                "params": {"msg": "DNS resolution failed for: {{ item.item }}"},
                "when": "not item.stdout",
                "loop": "{{ dns_results.results }}",
            },
        ],
        estimated_duration_seconds=20,
        risk_level="low",
    ),
    # ── Monitoring (3) ───────────────────────────────────────────────────
    Template(
        slug="log-collection",
        name="Collect Logs from Remote Hosts",
        description="Fetch recent log files from managed hosts to the control node",
        category="monitoring",
        tags=["logs", "collection", "audit", "troubleshooting"],
        required_vars=[
            TemplateVar(
                "log_paths",
                "list",
                "Log file paths to collect",
                default=["/var/log/syslog", "/var/log/auth.log"],
            ),
            TemplateVar(
                "local_dest", "str", "Local destination directory", default="/tmp/log-collection"
            ),
        ],
        tasks=[
            {
                "task_id": "t1",
                "name": "Create local destination directory",
                "module": "ansible.builtin.file",
                "params": {
                    "path": "{{ local_dest }}/{{ inventory_hostname }}",
                    "state": "directory",
                },
                "delegate_to": "localhost",
            },
            {
                "task_id": "t2",
                "name": "Check which logs exist",
                "module": "ansible.builtin.stat",
                "params": {"path": "{{ item }}"},
                "loop": "{{ log_paths }}",
                "register": "log_stats",
            },
            {
                "task_id": "t3",
                "name": "Fetch existing logs",
                "module": "ansible.builtin.fetch",
                "params": {"src": "{{ item.item }}", "dest": "{{ local_dest }}/", "flat": False},
                "when": "item.stat.exists",
                "loop": "{{ log_stats.results }}",
            },
            {
                "task_id": "t4",
                "name": "Report collected files",
                "module": "ansible.builtin.debug",
                "params": {
                    "msg": "Collected: {{ log_stats.results | selectattr('stat.exists') | map(attribute='item') | list }}"
                },
            },
        ],
        estimated_duration_seconds=30,
        risk_level="low",
    ),
    Template(
        slug="service-health-check",
        name="Multi-Service Health Check Report",
        description="Assert all specified services are running and return a health report",
        category="monitoring",
        tags=["health", "services", "monitoring", "report"],
        required_vars=[
            TemplateVar("services_to_check", "list", "Service names to verify", required=True),
        ],
        tasks=[
            {
                "task_id": "t1",
                "name": "Gather service facts",
                "module": "ansible.builtin.service_facts",
            },
            {
                "task_id": "t2",
                "name": "Check each service is running",
                "module": "ansible.builtin.debug",
                "params": {
                    "msg": "{{ item }}: {{ ansible_facts.services[item + '.service'].state | default('NOT FOUND') }}"
                },
                "loop": "{{ services_to_check }}",
            },
            {
                "task_id": "t3",
                "name": "Fail if any service is down",
                "module": "ansible.builtin.fail",
                "params": {
                    "msg": "Service {{ item }} is not running! State: {{ ansible_facts.services[item + '.service'].state | default('NOT FOUND') }}"
                },
                "when": "ansible_facts.services[item + '.service'] is not defined or ansible_facts.services[item + '.service'].state != 'running'",
                "loop": "{{ services_to_check }}",
            },
        ],
        estimated_duration_seconds=15,
        risk_level="low",
    ),
    Template(
        slug="disk-usage-report",
        name="Disk Usage and inode Report",
        description="Generate a disk usage report and fail if any mount exceeds the alert threshold",
        category="monitoring",
        tags=["disk", "monitoring", "capacity", "report"],
        required_vars=[
            TemplateVar("alert_threshold_pct", "int", "Alert if usage exceeds this %", default=85),
        ],
        tasks=[
            {
                "task_id": "t1",
                "name": "Get disk usage",
                "module": "ansible.builtin.command",
                "params": {"cmd": "df -h"},
                "register": "disk_usage",
            },
            {
                "task_id": "t2",
                "name": "Get inode usage",
                "module": "ansible.builtin.command",
                "params": {"cmd": "df -i"},
                "register": "inode_usage",
            },
            {
                "task_id": "t3",
                "name": "Display disk report",
                "module": "ansible.builtin.debug",
                "params": {"msg": "{{ disk_usage.stdout_lines }}"},
            },
            {
                "task_id": "t4",
                "name": "Check for high disk usage via facts",
                "module": "ansible.builtin.setup",
                "params": {"gather_subset": ["hardware"]},
            },
            {
                "task_id": "t5",
                "name": "Fail if disk usage too high",
                "module": "ansible.builtin.fail",
                "params": {
                    "msg": "Mount {{ item.mount }} is {{ ((item.size_total - item.size_available) / item.size_total * 100) | round(1) }}% full (threshold: {{ alert_threshold_pct }}%)"
                },
                "when": "item.size_total > 0 and ((item.size_total - item.size_available) / item.size_total * 100) > alert_threshold_pct",
                "loop": "{{ ansible_mounts }}",
            },
        ],
        estimated_duration_seconds=15,
        risk_level="low",
    ),
    # ── Deployment (3) ───────────────────────────────────────────────────
    Template(
        slug="deploy-docker-compose",
        name="Deploy Docker Compose Application",
        description="Pull images and start a Docker Compose application stack",
        category="deployment",
        tags=["docker", "deployment", "compose", "containers"],
        required_vars=[
            TemplateVar(
                "compose_file_path", required=True, description="Path to docker-compose.yml on host"
            ),
            TemplateVar("project_name", required=True, description="Docker Compose project name"),
            TemplateVar("pull_images", "bool", "Pull latest images before deploy", default=True),
            TemplateVar("service_port", "int", "Port to verify after deploy", default=80),
        ],
        tasks=[
            {
                "task_id": "t1",
                "name": "Check Docker is running",
                "module": "ansible.builtin.service",
                "params": {"name": "docker", "state": "started"},
            },
            {
                "task_id": "t2",
                "name": "Pull latest images",
                "module": "ansible.builtin.command",
                "params": {
                    "cmd": "docker compose -f {{ compose_file_path }} -p {{ project_name }} pull"
                },
                "when": "pull_images | bool",
            },
            {
                "task_id": "t3",
                "name": "Deploy / update stack",
                "module": "ansible.builtin.command",
                "params": {
                    "cmd": "docker compose -f {{ compose_file_path }} -p {{ project_name }} up -d --remove-orphans"
                },
            },
            {
                "task_id": "t4",
                "name": "Wait for service port",
                "module": "ansible.builtin.wait_for",
                "params": {
                    "host": "localhost",
                    "port": "{{ service_port }}",
                    "timeout": 60,
                    "state": "started",
                },
            },
            {
                "task_id": "t5",
                "name": "Verify containers are running",
                "module": "ansible.builtin.command",
                "params": {
                    "cmd": "docker compose -f {{ compose_file_path }} -p {{ project_name }} ps"
                },
                "register": "compose_status",
            },
            {
                "task_id": "t6",
                "name": "Show stack status",
                "module": "ansible.builtin.debug",
                "params": {"msg": "{{ compose_status.stdout_lines }}"},
            },
        ],
        estimated_duration_seconds=60,
        risk_level="medium",
    ),
    Template(
        slug="rolling-service-restart",
        name="Rolling Service Restart",
        description="Restart a service across hosts one-by-one with health check between each",
        category="deployment",
        tags=["restart", "rolling", "deployment", "zero-downtime"],
        required_vars=[
            TemplateVar("service_name", required=True, description="Systemd service name"),
            TemplateVar(
                "health_check_url", required=True, description="HTTP URL to verify after restart"
            ),
            TemplateVar(
                "health_check_retries", "int", "Number of health check retries", default=10
            ),
        ],
        tasks=[
            {
                "task_id": "t1",
                "name": "Restart service",
                "module": "ansible.builtin.systemd",
                "params": {
                    "name": "{{ service_name }}",
                    "state": "restarted",
                    "daemon_reload": False,
                },
            },
            {
                "task_id": "t2",
                "name": "Wait for service to start",
                "module": "ansible.builtin.wait_for",
                "params": {"timeout": 30, "delay": 5},
                "when": "health_check_url == ''",
            },
            {
                "task_id": "t3",
                "name": "Health check after restart",
                "module": "ansible.builtin.uri",
                "params": {"url": "{{ health_check_url }}", "status_code": [200], "timeout": 10},
                "register": "health_result",
                "retries": "{{ health_check_retries }}",
                "delay": 3,
                "until": "health_result.status == 200",
                "when": "health_check_url != ''",
            },
            {
                "task_id": "t4",
                "name": "Report restart status",
                "module": "ansible.builtin.debug",
                "params": {
                    "msg": "{{ service_name }} restarted successfully on {{ inventory_hostname }}"
                },
            },
        ],
        estimated_duration_seconds=45,
        risk_level="medium",
    ),
    Template(
        slug="freeipa-client-enroll",
        name="Enroll Host in FreeIPA",
        description="Install and configure FreeIPA client to join the identity domain",
        category="deployment",
        tags=["freeipa", "ipa", "identity", "enrollment", "ldap", "kerberos"],
        required_vars=[
            TemplateVar("ipa_server", required=True, description="FreeIPA server FQDN or IP"),
            TemplateVar(
                "ipa_domain", required=True, description="IPA domain (e.g. lab.pravesh.local)"
            ),
            TemplateVar(
                "ipa_realm", required=True, description="Kerberos realm (e.g. LAB.PRAVESH.LOCAL)"
            ),
            TemplateVar(
                "ipa_admin_password",
                required=True,
                description="IPA admin password (stored securely)",
            ),
        ],
        tasks=[
            {
                "task_id": "t1",
                "name": "Install FreeIPA client packages",
                "module": "ansible.builtin.apt",
                "params": {
                    "name": ["freeipa-client", "sssd", "sssd-tools"],
                    "state": "present",
                    "update_cache": True,
                },
                "when": "ansible_pkg_mgr == 'apt'",
            },
            {
                "task_id": "t2",
                "name": "Enroll host in FreeIPA",
                "module": "ansible.builtin.command",
                "params": {
                    "cmd": "ipa-client-install --server={{ ipa_server }} --domain={{ ipa_domain }} --realm={{ ipa_realm }} --principal=admin --password={{ ipa_admin_password }} --mkhomedir --no-ntp --unattended"
                },
                "no_log": True,
            },
            {
                "task_id": "t3",
                "name": "Enable SSSD service",
                "module": "ansible.builtin.service",
                "params": {"name": "sssd", "state": "started", "enabled": True},
            },
            {
                "task_id": "t4",
                "name": "Verify enrollment",
                "module": "ansible.builtin.command",
                "params": {"cmd": "id admin"},
                "register": "ipa_verify",
            },
            {
                "task_id": "t5",
                "name": "Show enrollment result",
                "module": "ansible.builtin.debug",
                "params": {"msg": "FreeIPA enrollment: {{ ipa_verify.stdout }}"},
            },
        ],
        estimated_duration_seconds=120,
        risk_level="medium",
    ),
]

# Build slug index
_TEMPLATE_INDEX: dict[str, Template] = {t.slug: t for t in TEMPLATES}
_TEMPLATE_CATEGORIES: list[str] = sorted({t.category for t in TEMPLATES})


def get_all_templates(category: str | None = None) -> list[Template]:
    if category:
        return [t for t in TEMPLATES if t.category == category]
    return TEMPLATES


def get_template(slug: str) -> Template | None:
    return _TEMPLATE_INDEX.get(slug)


def get_template_categories() -> list[str]:
    return _TEMPLATE_CATEGORIES
