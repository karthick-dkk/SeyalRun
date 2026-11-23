"""The built-in matcher: a host's installed packages (M1 facts) against the ACTIVE
feed snapshot (OSV affected ranges + OVAL patch definitions for its distro).

Pure: posture-service fetches the rows (app.scanning) and calls ``match``. It
runs after every successful facts collection and on demand.

Rules:
  * Version order is the distro's own: dpkg for Debian/Ubuntu, rpm EVR for the
    RHEL family (app.vercmp). Never string order.
  * Debian/Ubuntu advisories name SOURCE packages (openssl, gnutls28) while
    hosts install binaries (libssl3, libgnutls30); facts carry each binary's
    source package, and matching is done on the source name. RPM feeds name
    binaries, so RPM hosts match on the binary name.
  * Only advisories for the host's distro release apply: OSV ecosystem
    "Debian:12", "Ubuntu:22.04[:LTS]" (not Pro), "Rocky Linux:9",
    "AlmaLinux:9", "Red Hat:...enterprise_linux:9..."; OVAL platform
    "Debian GNU/Linux 12", "Ubuntu 22.04", "Red Hat Enterprise Linux 9".
  * One record per (vulnerability, package): if OSV and OVAL disagree on the
    fix, the higher fixed version wins (the one that fixes both).
"""

from __future__ import annotations

import re

from libs.pluginbase import HostRef, ScanRecord, VulnScanner

from ...risk import normalise_severity, worse
from ...vercmp import compare, in_range

RHEL_CLONES = {"rhel", "centos", "ol", "oracle", "redhat", "scientific", "eurolinux", "virtuozzo"}


def scheme_for(distro_family: str, pkg_manager: str = "") -> str | None:
    if distro_family == "debian" or pkg_manager == "dpkg":
        return "deb"
    if distro_family == "rhel" or pkg_manager == "rpm":
        return "rpm"
    return None


def _major(version: str) -> str:
    return (version or "").split(".", 1)[0]


def osv_like_patterns(distro_id: str, distro_family: str, distro_version: str) -> list[str]:
    """SQL LIKE patterns that narrow the OSV rows fetched for a host. A superset:
    ecosystem_matches() makes the exact decision."""
    did = (distro_id or "").lower()
    major = _major(distro_version)
    if not major:
        return []
    if did == "ubuntu":
        return [f"Ubuntu:{distro_version}%"]
    if did == "rocky":
        return [f"Rocky Linux:{major}%"]
    if did == "almalinux":
        return [f"AlmaLinux:{major}%"]
    if distro_family == "debian":
        return [f"Debian:{major}%"]
    if distro_family == "rhel":
        return [f"Red Hat:%enterprise_linux:{major}%"]
    return []


def oval_like_pattern(distro_id: str, distro_family: str, distro_version: str) -> str | None:
    did = (distro_id or "").lower()
    major = _major(distro_version)
    if not major:
        return None
    if did == "ubuntu":
        return f"%Ubuntu {distro_version}%"
    if distro_family == "debian":
        return f"%Debian GNU/Linux {major}%"
    if distro_family == "rhel":
        return f"%Red Hat Enterprise Linux {major}%"
    return None


def ecosystem_matches(eco: str, distro_id: str, distro_family: str, distro_version: str) -> bool:
    did = (distro_id or "").lower()
    major = _major(distro_version)
    parts = (eco or "").split(":")
    if did == "ubuntu":
        return parts[0] == "Ubuntu" and distro_version in parts[1:] and "Pro" not in parts
    if did == "debian" or (distro_family == "debian" and did != "ubuntu"):
        return parts[0] == "Debian" and len(parts) > 1 and parts[1].split(".")[0] == major
    if did == "rocky":
        return parts[0] == "Rocky Linux" and len(parts) > 1 and parts[1].split(".")[0] == major
    if did == "almalinux":
        return parts[0] == "AlmaLinux" and len(parts) > 1 and parts[1].split(".")[0] == major
    if distro_family == "rhel":
        return parts[0] == "Red Hat" and bool(re.search(rf"enterprise_linux:{re.escape(major)}(?:\D|$)", eco))
    return False


def oval_platform_matches(platform: str, distro_id: str, distro_family: str, distro_version: str) -> bool:
    did = (distro_id or "").lower()
    major = re.escape(_major(distro_version))
    if not platform or not major:
        return False
    if did == "ubuntu":
        return bool(re.search(rf"\bUbuntu {re.escape(distro_version)}(?![\d.])", platform))
    if distro_family == "debian":
        return bool(re.search(rf"\bDebian GNU/Linux {major}(?![\d])", platform))
    if distro_family == "rhel":
        return bool(re.search(rf"\bRed Hat Enterprise Linux {major}(?![\d])", platform))
    return False


def canonical_packages(installed: list[dict], scheme: str) -> dict[str, str]:
    """Installed rows -> {match name: version}. Debian: source package and source
    version when known. When several rows share a name (multi-arch, several
    binaries of one source) the LOWEST version is kept — the conservative answer."""
    out: dict[str, str] = {}
    for p in installed:
        if scheme == "deb":
            name = p.get("source") or p.get("name") or ""
            ver = p.get("source_version") or p.get("version") or ""
        else:
            name, ver = p.get("name") or "", p.get("version") or ""
        if not name or not ver:
            continue
        cur = out.get(name)
        if cur is None or compare(scheme, ver, cur) < 0:
            out[name] = ver
    return out


def binary_to_source(installed: list[dict]) -> dict[str, str]:
    """Debian: binary package name -> source package name."""
    return {p["name"]: p["source"] for p in installed if p.get("name") and p.get("source")}


class NativeScanner(VulnScanner):
    """Built-in matcher: installed packages vs the active OSV/OVAL feed snapshot."""

    name = "native"
    kind = "builtin"
    accepts_upload = False
    formats = ()

    def match(self, host: dict, packages: dict[str, str], osv_rows, oval_rows) -> list[ScanRecord]:
        """host: {host_id, hostname, distro_id, distro_family, distro_version,
        pkg_manager}; packages: canonical_packages(); rows: dicts shaped like
        the posture_feed_osv / posture_feed_oval tables."""
        scheme = scheme_for(host.get("distro_family", ""), host.get("pkg_manager", ""))
        if scheme is None:
            return []
        did, fam, ver = host.get("distro_id", ""), host.get("distro_family", ""), host.get("distro_version", "")
        found: dict[tuple[str, str], dict] = {}

        def add(vuln: str, pkg: str, fixed: str | None, sev: str | None, cvss, summary):
            key = (vuln, pkg)
            cur = found.get(key)
            nsev = normalise_severity(sev, cvss)
            if cur is None:
                found[key] = {"fixed": fixed, "sev": nsev, "cvss": cvss, "summary": summary}
                return
            if fixed and (not cur["fixed"] or compare(scheme, fixed, cur["fixed"]) > 0):
                cur["fixed"] = fixed
            cur["sev"] = worse(cur["sev"], nsev)
            if cvss is not None and (cur["cvss"] is None or cvss > cur["cvss"]):
                cur["cvss"] = cvss
            cur["summary"] = cur["summary"] or summary

        for r in osv_rows:
            pkg = r.get("package") or ""
            installed = packages.get(pkg)
            if installed is None or r.get("range_type") not in ("ECOSYSTEM", ""):
                continue
            if not ecosystem_matches(r.get("ecosystem", ""), did, fam, ver):
                continue
            if in_range(scheme, installed, r.get("introduced"), r.get("fixed"), r.get("last_affected")):
                add(r.get("cve_id") or r.get("vuln_id") or "", pkg, r.get("fixed"), r.get("severity"),
                    r.get("cvss"), r.get("summary"))
        for r in oval_rows:
            pkg = r.get("package") or ""
            installed = packages.get(pkg)
            fixed = r.get("fixed_version") or ""
            if installed is None or not fixed:
                continue
            if not oval_platform_matches(r.get("platform", ""), did, fam, ver):
                continue
            if compare(scheme, installed, fixed) < 0:
                add(r.get("cve_id") or r.get("definition_id") or "", pkg, fixed, r.get("severity"),
                    r.get("cvss"), r.get("summary"))

        ref = HostRef(host_id=host.get("host_id", ""), hostname=host.get("hostname", ""))
        return [ScanRecord(host=ref, cve_id=vuln[:64], package=pkg, installed_version=packages[pkg],
                           fixed_version=v["fixed"], severity=v["sev"], cvss=v["cvss"], summary=v["summary"])
                for (vuln, pkg), v in sorted(found.items()) if vuln]

    @staticmethod
    def covered(osv_rows, oval_rows, host: dict) -> bool:
        """Does the snapshot hold ANY advisory for this host's distro release?
        If not, "nothing matched" means "no data", and nothing may be marked fixed."""
        did, fam, ver = host.get("distro_id", ""), host.get("distro_family", ""), host.get("distro_version", "")
        return any(ecosystem_matches(r.get("ecosystem", ""), did, fam, ver) for r in osv_rows) or \
            any(oval_platform_matches(r.get("platform", ""), did, fam, ver) for r in oval_rows)
