"""Package and reboot operations posture-service may ask for (3.0 M3/M4).

posture-service names an operation and its arguments; it never supplies code.
Everything that reaches a host is (a) a script fixed in this image and (b) the
arguments validated here. Validation is done on this side, not trusted from the
caller, because this is the process that holds root credentials.
"""

from __future__ import annotations

import base64
import re

PACKAGE_ACTION = "package_ops"
REBOOT_ACTION = "host_reboot"
HOST_OP_PARAM = "_host_op"      # "_"-prefixed: filter_caller_params refuses it from any caller
OP_TOKEN_PARAM = "_op_token"

# Runs of these are never replayed by the generic re-run button: a re-run skips
# the rollout's approval, window and checks.
# Set on a run by the approve endpoint only; a continuation of the same operation keeps it.
APPROVED_BY_PARAM = "_approved_by"
NO_RERUN_ACTIONS = frozenset({PACKAGE_ACTION, REBOOT_ACTION})

READ_ONLY_OPS = frozenset({"plan", "snapshot"})
PACKAGE_OPS = frozenset({"plan", "refresh", "install", "upgrade", "remove", "purge", "hold", "unhold",
                         "pin", "rollback", "repo_apply"})
REBOOT_OPS = frozenset({"snapshot", "reboot"})
NEEDS_PACKAGES = frozenset({"plan", "install", "upgrade", "remove", "purge", "hold", "unhold", "pin", "rollback"})
NEEDS_VERSION = frozenset({"pin", "rollback"})
MAX_PACKAGES = 2000

# Starts alphanumeric, then only what Debian and RPM names use. No "/" (apt reads
# "./x.deb" as a local file), no leading "-" (an option), no glob, no "=".
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,199}\Z")
_ARCH = re.compile(r"[a-z0-9_]{1,20}\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+:~^_-]{0,199}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{7,79}\Z")
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,39}\Z")
_URL = re.compile(r"https?://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[A-Za-z0-9._~/%+$-]*)?\Z")
_SUITE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,79}\Z")
_HEALTH = re.compile(r"https?://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[A-Za-z0-9._~/%+?&=-]*)?\Z")
_KEY = re.compile(r"-----BEGIN PGP PUBLIC KEY BLOCK-----[A-Za-z0-9+/=\s:.,()@<>_-]+-----END PGP PUBLIC KEY BLOCK-----\s*\Z")

MAX_ARGV_BYTES = 100_000

# Removing one of these leaves a host that cannot be reached or repaired.
PROTECTED = frozenset({
    "apt", "bash", "coreutils", "coreutils-single", "dbus", "dnf", "dpkg",
    "filesystem", "glibc", "grep", "grub2-common", "init", "kernel", "kernel-core",
    "kernel-default", "libc6", "libzypp", "login", "microdnf", "openssh", "openssh-server",
    "pam", "passwd", "rpm", "shadow", "shadow-utils", "sudo", "systemd",
    "systemd-sysv", "util-linux", "yum", "zypper",
})


class HostOpError(ValueError):
    """The requested operation is not one this service will run."""


def check_token(token: str) -> str:
    if not isinstance(token, str) or not _TOKEN.match(token):
        raise HostOpError("invalid op token")
    return token


def _package_arg(op: str, pkg: dict) -> str:
    """name | name:arch | name=version | name:arch=version — one argv word."""
    if not isinstance(pkg, dict):
        raise HostOpError("each package must be an object")
    name, arch, version = pkg.get("name") or "", pkg.get("arch") or "", pkg.get("version") or ""
    if not isinstance(name, str) or not _NAME.match(name):
        raise HostOpError(f"invalid package name {str(name)[:60]!r}")
    if arch and (not isinstance(arch, str) or not _ARCH.match(arch)):
        raise HostOpError(f"invalid architecture for {name}")
    if version and (not isinstance(version, str) or not _VERSION.match(version)):
        raise HostOpError(f"invalid version for {name}")
    if op in NEEDS_VERSION and not version:
        raise HostOpError(f"{op} needs a version for {name}")
    if op not in NEEDS_VERSION and op not in ("install", "upgrade", "plan") and version:
        raise HostOpError(f"{op} does not take a version ({name})")
    if op in ("remove", "purge") and name in PROTECTED:
        raise HostOpError(f"{name} is protected and cannot be removed")
    return name + (f":{arch}" if arch else "") + (f"={version}" if version else "")


def render_repo(repo: dict) -> list[tuple[str, str, str]]:
    """[(target, filename, content)] for one repo. Signature checking is not
    optional: a mirror without a key is refused, not configured unsigned."""
    slug, family, url = repo.get("slug") or "", repo.get("family") or "", repo.get("base_url") or ""
    key = repo.get("gpg_key") or ""
    if not _SLUG.match(slug):
        raise HostOpError("invalid repo slug")
    if family not in ("deb", "rpm", "zypp"):
        raise HostOpError("repo family must be deb, rpm or zypp")
    if not _URL.match(url):
        raise HostOpError(f"invalid repo URL for {slug}")
    if len(key) > 65536 or not _KEY.match(key.strip() + "\n"):
        raise HostOpError(f"repo {slug} needs an ASCII-armoured OpenPGP public key")
    out = [("key", f"seyalrun-{slug}.asc", key.strip() + "\n")]
    if family == "deb":
        suite, comps = repo.get("suite") or "", (repo.get("components") or "").split()
        if not _SUITE.match(suite) or not all(_SUITE.match(c) for c in comps):
            raise HostOpError(f"invalid suite or components for {slug}")
        flat = suite.endswith("/")
        if not flat and not comps:
            raise HostOpError(f"repo {slug} needs at least one component")
        body = f"Types: deb\nURIs: {url}\nSuites: {suite}\n"
        if not flat:
            body += f"Components: {' '.join(comps)}\n"
        body += f"Signed-By: /etc/apt/keyrings/seyalrun-{slug}.asc\n"
        out.append(("deb", f"seyalrun-{slug}.sources", body))
    else:
        keydir = "/etc/pki/rpm-gpg" if family == "rpm" else "/etc/zypp/seyalrun-keys"
        body = (f"[seyalrun-{slug}]\nname=SeyalRun {slug}\nbaseurl={url}\nenabled=1\ngpgcheck=1\n"
                f"gpgkey=file://{keydir}/seyalrun-{slug}.asc\n")
        out.append((family, f"seyalrun-{slug}.repo", body))
    return out


def package_argv(op: str, args: dict) -> list[str]:
    """Validated worker arguments for package_ops.sh: op, repo mode, then items."""
    if op not in PACKAGE_OPS:
        raise HostOpError(f"unknown package operation {str(op)[:40]!r}")
    unknown = set(args) - {"packages", "repo_mode", "repos"}
    if unknown:
        raise HostOpError("unknown argument(s): " + ", ".join(sorted(unknown)))
    mode = args.get("repo_mode") or "host"
    if mode not in ("host", "managed"):
        raise HostOpError("repo_mode must be host or managed")
    argv = [op, mode]
    if op == "repo_apply":
        repos = args.get("repos")
        if not isinstance(repos, list) or len(repos) > 40:
            raise HostOpError("repo_apply needs a list of at most 40 repos")
        slugs = [r.get("slug") for r in repos if isinstance(r, dict)]
        if len(slugs) != len(repos) or len(set(slugs)) != len(slugs):
            raise HostOpError("repo slugs must be unique")
        for repo in repos:
            for target, fname, content in render_repo(repo):
                argv.append(f"{target}:{fname}:{base64.b64encode(content.encode()).decode()}")
        # sshd hands the whole command to the shell as ONE argument; Linux caps that at 128 KiB.
        if sum(len(a) + 3 for a in argv) > MAX_ARGV_BYTES:
            raise HostOpError("the repo definitions are too large for one run: use smaller signing keys or fewer repos")
        return argv
    pkgs = args.get("packages") or []
    if not isinstance(pkgs, list) or len(pkgs) > MAX_PACKAGES:
        raise HostOpError(f"packages must be a list of at most {MAX_PACKAGES}")
    if op in NEEDS_PACKAGES and not pkgs:
        raise HostOpError(f"{op} needs at least one package")
    return argv + list(dict.fromkeys(_package_arg(op, p) for p in pkgs))


def reboot_argv(op: str, args: dict) -> list[str]:
    if op not in REBOOT_OPS:
        raise HostOpError(f"unknown reboot operation {str(op)[:40]!r}")
    unknown = set(args) - {"health_urls", "wait_seconds"}
    if unknown:
        raise HostOpError("unknown argument(s): " + ", ".join(sorted(unknown)))
    urls = args.get("health_urls") or []
    if not isinstance(urls, list) or len(urls) > 10 or not all(isinstance(u, str) and _HEALTH.match(u) for u in urls):
        raise HostOpError("health_urls must be at most 10 plain http(s) URLs")
    return [op, *urls]


def argv_for(action: str, op: str, args: dict) -> list[str]:
    if action == PACKAGE_ACTION:
        return package_argv(op, args)
    if action == REBOOT_ACTION:
        return reboot_argv(op, args)
    raise HostOpError("unknown action")


def wait_seconds(args: dict) -> int:
    return max(60, min(int(args.get("wait_seconds") or 900), 3000))
