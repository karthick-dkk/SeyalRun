"""Static Ansible module catalog — 30 most-used Linux admin modules.

No subprocess, no ansible-doc. Static definitions drive the UI param forms.
"""

from __future__ import annotations

from ..models.schemas import ModuleInfo, ParamSchema

_MODULES: list[ModuleInfo] = [
    # ── Package Management ────────────────────────────────────────────────
    ModuleInfo(
        name="ansible.builtin.apt",
        short_name="apt",
        category="packages",
        description="Manage apt packages on Debian/Ubuntu systems",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/apt_module.html",
        params=[
            ParamSchema(
                name="name",
                type="list",
                required=True,
                description="Package name(s)",
                example=["nginx", "curl"],
            ),
            ParamSchema(
                name="state",
                type="str",
                required=True,
                default="present",
                choices=["present", "absent", "latest", "build-dep", "fixed"],
            ),
            ParamSchema(
                name="update_cache",
                type="bool",
                default=False,
                description="Run apt-get update before installing",
            ),
            ParamSchema(
                name="cache_valid_time",
                type="int",
                default=3600,
                description="Cache validity in seconds",
            ),
            ParamSchema(
                name="purge",
                type="bool",
                default=False,
                description="Remove config files on removal",
            ),
            ParamSchema(
                name="autoremove",
                type="bool",
                default=False,
                description="Remove dependencies no longer needed",
            ),
        ],
        example_task={
            "name": "Install nginx",
            "ansible.builtin.apt": {"name": ["nginx"], "state": "present", "update_cache": True},
        },
    ),
    ModuleInfo(
        name="ansible.builtin.yum",
        short_name="yum",
        category="packages",
        description="Manage yum packages on RHEL/CentOS 7 and older",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/yum_module.html",
        params=[
            ParamSchema(
                name="name",
                type="list",
                required=True,
                description="Package name(s)",
                example=["httpd"],
            ),
            ParamSchema(
                name="state",
                type="str",
                required=True,
                default="present",
                choices=["present", "absent", "latest", "removed"],
            ),
            ParamSchema(name="update_cache", type="bool", default=False),
            ParamSchema(
                name="security",
                type="bool",
                default=False,
                description="Install only security updates",
            ),
        ],
        example_task={
            "name": "Install httpd",
            "ansible.builtin.yum": {"name": ["httpd"], "state": "present"},
        },
    ),
    ModuleInfo(
        name="ansible.builtin.dnf",
        short_name="dnf",
        category="packages",
        description="Manage dnf packages on RHEL 8+ / Fedora",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/dnf_module.html",
        params=[
            ParamSchema(name="name", type="list", required=True, example=["httpd"]),
            ParamSchema(
                name="state",
                type="str",
                required=True,
                default="present",
                choices=["present", "absent", "latest"],
            ),
            ParamSchema(
                name="update_only",
                type="bool",
                default=False,
                description="Only update, do not install",
            ),
            ParamSchema(name="security", type="bool", default=False),
        ],
        example_task={
            "name": "Install httpd via dnf",
            "ansible.builtin.dnf": {"name": ["httpd"], "state": "present"},
        },
    ),
    ModuleInfo(
        name="ansible.builtin.package",
        short_name="package",
        category="packages",
        description="Generic OS-agnostic package manager",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/package_module.html",
        params=[
            ParamSchema(name="name", type="str", required=True, example="curl"),
            ParamSchema(
                name="state",
                type="str",
                required=True,
                default="present",
                choices=["present", "absent", "latest"],
            ),
        ],
        example_task={
            "name": "Ensure curl is installed",
            "ansible.builtin.package": {"name": "curl", "state": "present"},
        },
    ),
    ModuleInfo(
        name="ansible.builtin.apt_repository",
        short_name="apt_repository",
        category="packages",
        description="Add or remove APT repositories",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/apt_repository_module.html",
        params=[
            ParamSchema(
                name="repo",
                type="str",
                required=True,
                description="APT repository line",
                example="ppa:nginx/stable",
            ),
            ParamSchema(name="state", type="str", default="present", choices=["present", "absent"]),
            ParamSchema(name="update_cache", type="bool", default=True),
        ],
        example_task={
            "name": "Add nginx PPA",
            "ansible.builtin.apt_repository": {"repo": "ppa:nginx/stable", "state": "present"},
        },
    ),
    # ── System & Services ─────────────────────────────────────────────────
    ModuleInfo(
        name="ansible.builtin.service",
        short_name="service",
        category="services",
        description="Manage services (start, stop, restart, enable/disable)",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/service_module.html",
        params=[
            ParamSchema(name="name", type="str", required=True, example="nginx"),
            ParamSchema(
                name="state",
                type="str",
                required=True,
                choices=["started", "stopped", "restarted", "reloaded"],
            ),
            ParamSchema(name="enabled", type="bool", description="Whether to start on boot"),
            ParamSchema(name="daemon_reload", type="bool", default=False),
        ],
        example_task={
            "name": "Start and enable nginx",
            "ansible.builtin.service": {"name": "nginx", "state": "started", "enabled": True},
        },
    ),
    ModuleInfo(
        name="ansible.builtin.systemd",
        short_name="systemd",
        category="services",
        description="Manage systemd units with daemon-reload support",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/systemd_module.html",
        params=[
            ParamSchema(name="name", type="str", required=True, example="nginx.service"),
            ParamSchema(
                name="state", type="str", choices=["started", "stopped", "restarted", "reloaded"]
            ),
            ParamSchema(name="enabled", type="bool"),
            ParamSchema(
                name="daemon_reload",
                type="bool",
                default=False,
                description="Run daemon-reload before action",
            ),
            ParamSchema(name="masked", type="bool", default=False),
        ],
        example_task={
            "name": "Reload systemd and restart nginx",
            "ansible.builtin.systemd": {
                "name": "nginx",
                "state": "restarted",
                "daemon_reload": True,
            },
        },
    ),
    ModuleInfo(
        name="ansible.builtin.reboot",
        short_name="reboot",
        category="services",
        description="Reboot hosts and wait for them to come back",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/reboot_module.html",
        params=[
            ParamSchema(
                name="reboot_timeout",
                type="int",
                default=600,
                description="Seconds to wait for reboot",
            ),
            ParamSchema(name="msg", type="str", default="Reboot initiated by Ansible"),
            ParamSchema(name="pre_reboot_delay", type="int", default=0),
            ParamSchema(name="post_reboot_delay", type="int", default=30),
        ],
        example_task={
            "name": "Reboot if required",
            "ansible.builtin.reboot": {"reboot_timeout": 300, "msg": "Applying kernel update"},
        },
    ),
    ModuleInfo(
        name="ansible.builtin.cron",
        short_name="cron",
        category="services",
        description="Manage cron.d and crontab entries",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/cron_module.html",
        params=[
            ParamSchema(
                name="name", type="str", required=True, description="Unique cron entry identifier"
            ),
            ParamSchema(
                name="job", type="str", description="Command to run", example="/usr/bin/backup.sh"
            ),
            ParamSchema(name="minute", type="str", default="0"),
            ParamSchema(name="hour", type="str", default="2"),
            ParamSchema(name="day", type="str", default="*"),
            ParamSchema(name="month", type="str", default="*"),
            ParamSchema(name="weekday", type="str", default="*"),
            ParamSchema(name="state", type="str", default="present", choices=["present", "absent"]),
            ParamSchema(name="user", type="str", default="root"),
        ],
        example_task={
            "name": "Schedule nightly backup",
            "ansible.builtin.cron": {
                "name": "nightly-backup",
                "job": "/usr/bin/backup.sh",
                "hour": "2",
                "minute": "0",
            },
        },
    ),
    ModuleInfo(
        name="ansible.builtin.user",
        short_name="user",
        category="services",
        description="Manage Linux user accounts",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/user_module.html",
        params=[
            ParamSchema(name="name", type="str", required=True, description="Username"),
            ParamSchema(name="state", type="str", default="present", choices=["present", "absent"]),
            ParamSchema(name="uid", type="int"),
            ParamSchema(name="groups", type="list", description="Supplementary groups"),
            ParamSchema(name="shell", type="str", default="/bin/bash"),
            ParamSchema(name="home", type="path"),
            ParamSchema(name="create_home", type="bool", default=True),
            ParamSchema(name="comment", type="str", description="GECOS/full name"),
            ParamSchema(name="password_lock", type="bool", default=False),
            ParamSchema(name="system", type="bool", default=False),
        ],
        example_task={
            "name": "Create deploy user",
            "ansible.builtin.user": {
                "name": "deploy",
                "groups": ["sudo"],
                "shell": "/bin/bash",
                "create_home": True,
            },
        },
    ),
    ModuleInfo(
        name="ansible.builtin.group",
        short_name="group",
        category="services",
        description="Manage Linux groups",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/group_module.html",
        params=[
            ParamSchema(name="name", type="str", required=True),
            ParamSchema(name="state", type="str", default="present", choices=["present", "absent"]),
            ParamSchema(name="gid", type="int"),
            ParamSchema(name="system", type="bool", default=False),
        ],
        example_task={
            "name": "Create devops group",
            "ansible.builtin.group": {"name": "devops", "state": "present"},
        },
    ),
    # ── File Operations ───────────────────────────────────────────────────
    ModuleInfo(
        name="ansible.builtin.file",
        short_name="file",
        category="files",
        description="Create/delete files, directories, symlinks and set permissions",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/file_module.html",
        params=[
            ParamSchema(name="path", type="path", required=True, example="/etc/myapp"),
            ParamSchema(
                name="state",
                type="str",
                required=True,
                choices=["file", "directory", "link", "hard", "touch", "absent"],
            ),
            ParamSchema(name="owner", type="str"),
            ParamSchema(name="group", type="str"),
            ParamSchema(
                name="mode", type="str", description="Permissions (e.g. '0644')", example="0755"
            ),
            ParamSchema(name="recurse", type="bool", default=False),
            ParamSchema(name="src", type="path", description="Source for symlinks"),
        ],
        example_task={
            "name": "Create app directory",
            "ansible.builtin.file": {
                "path": "/opt/myapp",
                "state": "directory",
                "owner": "app",
                "mode": "0755",
            },
        },
    ),
    ModuleInfo(
        name="ansible.builtin.copy",
        short_name="copy",
        category="files",
        description="Copy files to remote hosts",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/copy_module.html",
        params=[
            ParamSchema(name="src", type="path", description="Local source file path"),
            ParamSchema(
                name="content", type="str", description="Inline content (alternative to src)"
            ),
            ParamSchema(name="dest", type="path", required=True, example="/etc/myapp/config.conf"),
            ParamSchema(name="owner", type="str"),
            ParamSchema(name="group", type="str"),
            ParamSchema(name="mode", type="str", example="0644"),
            ParamSchema(name="backup", type="bool", default=False),
        ],
        example_task={
            "name": "Copy config file",
            "ansible.builtin.copy": {
                "src": "files/config.conf",
                "dest": "/etc/myapp/config.conf",
                "mode": "0644",
            },
        },
    ),
    ModuleInfo(
        name="ansible.builtin.template",
        short_name="template",
        category="files",
        description="Deploy Jinja2 templates with variable substitution",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/template_module.html",
        params=[
            ParamSchema(
                name="src", type="path", required=True, description="Local .j2 template path"
            ),
            ParamSchema(name="dest", type="path", required=True),
            ParamSchema(name="owner", type="str"),
            ParamSchema(name="group", type="str"),
            ParamSchema(name="mode", type="str"),
            ParamSchema(name="backup", type="bool", default=False),
        ],
        example_task={
            "name": "Deploy nginx config",
            "ansible.builtin.template": {
                "src": "templates/nginx.conf.j2",
                "dest": "/etc/nginx/nginx.conf",
                "mode": "0644",
            },
        },
    ),
    ModuleInfo(
        name="ansible.builtin.lineinfile",
        short_name="lineinfile",
        category="files",
        description="Ensure a specific line exists (or does not exist) in a file",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/lineinfile_module.html",
        params=[
            ParamSchema(name="path", type="path", required=True, example="/etc/ssh/sshd_config"),
            ParamSchema(
                name="regexp",
                type="str",
                description="Regex to match the line",
                example="^PasswordAuthentication",
            ),
            ParamSchema(
                name="line",
                type="str",
                description="Line to insert/replace",
                example="PasswordAuthentication no",
            ),
            ParamSchema(name="state", type="str", default="present", choices=["present", "absent"]),
            ParamSchema(name="insertafter", type="str"),
            ParamSchema(name="insertbefore", type="str"),
            ParamSchema(name="create", type="bool", default=False),
            ParamSchema(name="backup", type="bool", default=False),
        ],
        example_task={
            "name": "Disable SSH password auth",
            "ansible.builtin.lineinfile": {
                "path": "/etc/ssh/sshd_config",
                "regexp": "^#?PasswordAuthentication",
                "line": "PasswordAuthentication no",
            },
        },
    ),
    ModuleInfo(
        name="ansible.builtin.blockinfile",
        short_name="blockinfile",
        category="files",
        description="Insert, update, or remove a block of lines in a file",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/blockinfile_module.html",
        params=[
            ParamSchema(name="path", type="path", required=True),
            ParamSchema(
                name="block", type="str", required=True, description="Block of text to insert"
            ),
            ParamSchema(name="marker", type="str", default="# {mark} ANSIBLE MANAGED BLOCK"),
            ParamSchema(name="insertafter", type="str"),
            ParamSchema(name="insertbefore", type="str"),
            ParamSchema(name="state", type="str", default="present", choices=["present", "absent"]),
            ParamSchema(name="create", type="bool", default=False),
        ],
        example_task={
            "name": "Add hosts entries",
            "ansible.builtin.blockinfile": {
                "path": "/etc/hosts",
                "block": "192.168.1.10 db.internal\n192.168.1.11 cache.internal",
            },
        },
    ),
    ModuleInfo(
        name="ansible.builtin.fetch",
        short_name="fetch",
        category="files",
        description="Retrieve files from remote hosts to the control node",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/fetch_module.html",
        params=[
            ParamSchema(
                name="src",
                type="path",
                required=True,
                description="Remote file to fetch",
                example="/var/log/app.log",
            ),
            ParamSchema(
                name="dest", type="path", required=True, description="Local destination directory"
            ),
            ParamSchema(
                name="flat", type="bool", default=False, description="Flatten directory structure"
            ),
            ParamSchema(name="fail_on_missing", type="bool", default=True),
        ],
        example_task={
            "name": "Fetch application logs",
            "ansible.builtin.fetch": {
                "src": "/var/log/app.log",
                "dest": "/tmp/logs/",
                "flat": False,
            },
        },
    ),
    # ── Command Execution ─────────────────────────────────────────────────
    ModuleInfo(
        name="ansible.builtin.command",
        short_name="command",
        category="commands",
        description="Execute a command without shell expansion (safer than shell)",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/command_module.html",
        params=[
            ParamSchema(name="cmd", type="str", required=True, example="id"),
            ParamSchema(name="chdir", type="path", description="Change directory before running"),
            ParamSchema(name="creates", type="path", description="Skip if this file exists"),
            ParamSchema(
                name="removes", type="path", description="Skip if this file does not exist"
            ),
            ParamSchema(
                name="argv", type="list", description="Command as list (alternative to cmd)"
            ),
        ],
        example_task={"name": "Check disk usage", "ansible.builtin.command": {"cmd": "df -h /"}},
    ),
    ModuleInfo(
        name="ansible.builtin.shell",
        short_name="shell",
        category="commands",
        description="Execute shell commands with pipes, redirects, and expansions",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/shell_module.html",
        params=[
            ParamSchema(name="cmd", type="str", required=True, example="echo $HOME | grep root"),
            ParamSchema(name="chdir", type="path"),
            ParamSchema(name="executable", type="path", default="/bin/sh"),
            ParamSchema(name="creates", type="path"),
            ParamSchema(name="removes", type="path"),
        ],
        example_task={
            "name": "Get running docker containers",
            "ansible.builtin.shell": {"cmd": "docker ps --format '{{.Names}}' | wc -l"},
        },
    ),
    ModuleInfo(
        name="ansible.builtin.raw",
        short_name="raw",
        category="commands",
        description="Execute a raw SSH command (useful for bootstrapping)",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/raw_module.html",
        params=[
            ParamSchema(
                name="_raw_params", type="str", required=True, example="apt-get install -y python3"
            ),
            ParamSchema(name="executable", type="path"),
        ],
        example_task={
            "name": "Bootstrap Python on target",
            "ansible.builtin.raw": "apt-get install -y python3 2>/dev/null || yum install -y python3",
        },
    ),
    # ── Networking ────────────────────────────────────────────────────────
    ModuleInfo(
        name="ansible.posix.firewalld",
        short_name="firewalld",
        category="networking",
        description="Manage firewalld rules on RHEL/CentOS/Fedora",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/posix/firewalld_module.html",
        params=[
            ParamSchema(
                name="service",
                type="str",
                description="Service name (e.g. 'http')",
                example="https",
            ),
            ParamSchema(name="port", type="str", description="Port/protocol (e.g. '8080/tcp')"),
            ParamSchema(name="state", type="str", required=True, choices=["enabled", "disabled"]),
            ParamSchema(name="permanent", type="bool", default=True),
            ParamSchema(name="immediate", type="bool", default=True),
            ParamSchema(name="zone", type="str", default="public"),
        ],
        example_task={
            "name": "Allow HTTPS through firewalld",
            "ansible.posix.firewalld": {
                "service": "https",
                "state": "enabled",
                "permanent": True,
                "immediate": True,
            },
        },
    ),
    ModuleInfo(
        name="community.general.ufw",
        short_name="ufw",
        category="networking",
        description="Manage UFW firewall rules on Ubuntu/Debian",
        docs_url="https://docs.ansible.com/ansible/latest/collections/community/general/ufw_module.html",
        params=[
            ParamSchema(name="rule", type="str", choices=["allow", "deny", "reject", "limit"]),
            ParamSchema(name="port", type="str", example="443"),
            ParamSchema(name="proto", type="str", choices=["tcp", "udp", "any"], default="tcp"),
            ParamSchema(
                name="state", type="str", choices=["enabled", "disabled", "reloaded", "reset"]
            ),
            ParamSchema(name="direction", type="str", choices=["in", "out", "routed"]),
            ParamSchema(name="comment", type="str"),
        ],
        example_task={
            "name": "Allow HTTPS via UFW",
            "community.general.ufw": {
                "rule": "allow",
                "port": "443",
                "proto": "tcp",
                "comment": "HTTPS",
            },
        },
    ),
    ModuleInfo(
        name="ansible.builtin.uri",
        short_name="uri",
        category="networking",
        description="Interact with HTTP/HTTPS URLs (health checks, API calls)",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/uri_module.html",
        params=[
            ParamSchema(name="url", type="str", required=True, example="http://localhost/health"),
            ParamSchema(
                name="method",
                type="str",
                default="GET",
                choices=["GET", "POST", "PUT", "DELETE", "HEAD"],
            ),
            ParamSchema(name="status_code", type="list", default=[200]),
            ParamSchema(name="timeout", type="int", default=30),
            ParamSchema(name="validate_certs", type="bool", default=True),
            ParamSchema(name="body", type="str"),
            ParamSchema(name="headers", type="dict"),
            ParamSchema(name="return_content", type="bool", default=False),
        ],
        example_task={
            "name": "Check service health",
            "ansible.builtin.uri": {
                "url": "http://localhost:8080/health",
                "status_code": [200],
                "timeout": 10,
            },
        },
    ),
    ModuleInfo(
        name="ansible.builtin.wait_for",
        short_name="wait_for",
        category="networking",
        description="Wait for a port, file, or condition to become true",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/wait_for_module.html",
        params=[
            ParamSchema(name="host", type="str", default="127.0.0.1"),
            ParamSchema(name="port", type="int", description="Port to wait for"),
            ParamSchema(name="path", type="path", description="File path to wait for"),
            ParamSchema(
                name="state",
                type="str",
                default="started",
                choices=["started", "stopped", "present", "absent", "drained"],
            ),
            ParamSchema(name="timeout", type="int", default=300),
            ParamSchema(name="delay", type="int", default=0),
            ParamSchema(name="sleep", type="int", default=1),
        ],
        example_task={
            "name": "Wait for port 8080 to open",
            "ansible.builtin.wait_for": {"host": "localhost", "port": 8080, "timeout": 60},
        },
    ),
    # ── Security ──────────────────────────────────────────────────────────
    ModuleInfo(
        name="ansible.posix.authorized_key",
        short_name="authorized_key",
        category="security",
        description="Manage SSH authorized_keys file entries",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/posix/authorized_key_module.html",
        params=[
            ParamSchema(name="user", type="str", required=True, example="deploy"),
            ParamSchema(
                name="key", type="str", required=True, description="SSH public key string or URL"
            ),
            ParamSchema(name="state", type="str", default="present", choices=["present", "absent"]),
            ParamSchema(
                name="exclusive",
                type="bool",
                default=False,
                description="Remove all other keys for this user",
            ),
            ParamSchema(name="manage_dir", type="bool", default=True),
        ],
        example_task={
            "name": "Add SSH key for deploy user",
            "ansible.posix.authorized_key": {
                "user": "deploy",
                "key": "{{ lookup('file', '~/.ssh/id_rsa.pub') }}",
                "state": "present",
            },
        },
    ),
    ModuleInfo(
        name="community.crypto.openssl_certificate_info",
        short_name="openssl_certificate_info",
        category="security",
        description="Inspect SSL/TLS certificate information and expiry",
        docs_url="https://docs.ansible.com/ansible/latest/collections/community/crypto/openssl_certificate_info_module.html",
        params=[
            ParamSchema(
                name="path", type="path", required=True, example="/etc/ssl/certs/server.crt"
            ),
            ParamSchema(
                name="valid_at", type="dict", description="Check validity at specific times"
            ),
        ],
        example_task={
            "name": "Check cert expiry",
            "community.crypto.openssl_certificate_info": {"path": "/etc/ssl/certs/server.crt"},
            "register": "cert_info",
        },
    ),
    ModuleInfo(
        name="community.crypto.x509_certificate",
        short_name="x509_certificate",
        category="security",
        description="Generate or renew X.509 / SSL certificates",
        docs_url="https://docs.ansible.com/ansible/latest/collections/community/crypto/x509_certificate_module.html",
        params=[
            ParamSchema(
                name="path", type="path", required=True, description="Certificate destination path"
            ),
            ParamSchema(name="privatekey_path", type="path", required=True),
            ParamSchema(name="csr_path", type="path"),
            ParamSchema(
                name="provider", type="str", required=True, choices=["selfsigned", "ownca", "acme"]
            ),
            ParamSchema(name="selfsigned_not_after", type="str", default="+3650d"),
        ],
        example_task={
            "name": "Generate self-signed cert",
            "community.crypto.x509_certificate": {
                "path": "/etc/ssl/certs/myapp.crt",
                "privatekey_path": "/etc/ssl/private/myapp.key",
                "provider": "selfsigned",
            },
        },
    ),
    # ── Diagnostics ───────────────────────────────────────────────────────
    ModuleInfo(
        name="ansible.builtin.setup",
        short_name="setup",
        category="diagnostics",
        description="Gather facts from remote hosts (hardware, OS, network, etc.)",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/setup_module.html",
        params=[
            ParamSchema(
                name="filter",
                type="str",
                description="Fact filter pattern",
                example="ansible_distribution*",
            ),
            ParamSchema(
                name="gather_subset",
                type="list",
                description="Subset of facts to gather",
                default=["all"],
            ),
            ParamSchema(name="gather_timeout", type="int", default=10),
        ],
        example_task={
            "name": "Gather all facts",
            "ansible.builtin.setup": {"gather_subset": ["all"]},
        },
    ),
    ModuleInfo(
        name="ansible.builtin.debug",
        short_name="debug",
        category="diagnostics",
        description="Print variables and messages during playbook execution",
        docs_url="https://docs.ansible.com/ansible/latest/collections/ansible/builtin/debug_module.html",
        params=[
            ParamSchema(
                name="msg",
                type="str",
                description="Message to print",
                example="Current user is {{ ansible_user }}",
            ),
            ParamSchema(name="var", type="str", description="Variable name to debug"),
            ParamSchema(
                name="verbosity", type="int", default=0, description="Minimum verbosity level"
            ),
        ],
        example_task={
            "name": "Print OS info",
            "ansible.builtin.debug": {
                "msg": "OS: {{ ansible_distribution }} {{ ansible_distribution_version }}"
            },
        },
    ),
    ModuleInfo(
        name="community.general.filesystem",
        short_name="filesystem",
        category="diagnostics",
        description="Create or resize filesystems on block devices",
        docs_url="https://docs.ansible.com/ansible/latest/collections/community/general/filesystem_module.html",
        params=[
            ParamSchema(
                name="fstype",
                type="str",
                required=True,
                choices=["ext2", "ext3", "ext4", "xfs", "btrfs", "vfat"],
                example="ext4",
            ),
            ParamSchema(name="dev", type="path", required=True, example="/dev/sdb1"),
            ParamSchema(name="resizefs", type="bool", default=False),
            ParamSchema(name="force", type="bool", default=False),
        ],
        example_task={
            "name": "Create ext4 filesystem",
            "community.general.filesystem": {"fstype": "ext4", "dev": "/dev/sdb1"},
        },
    ),
]

# Build lookup index by name
_MODULE_INDEX: dict[str, ModuleInfo] = {m.name: m for m in _MODULES}
_MODULE_CATEGORIES: list[str] = sorted({m.category for m in _MODULES})


def get_all_modules(category: str | None = None) -> list[ModuleInfo]:
    if category:
        return [m for m in _MODULES if m.category == category]
    return _MODULES


def get_module(name: str) -> ModuleInfo | None:
    return _MODULE_INDEX.get(name)


def get_categories() -> list[str]:
    return _MODULE_CATEGORIES
