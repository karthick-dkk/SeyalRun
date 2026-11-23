"""Generic result formats: CSV, CycloneDX VEX JSON and SARIF 2.1.

For scanners SeyalRun has no dedicated plugin for. The format is detected from
the content, not the file name.

CSV — header row, case-insensitive, aliases accepted:
    host | hostname | fqdn | ip | host_id   (at least one of them, or a host_id
                                            override on the upload)
    cve | cve_id | vulnerability            (required)
    package | pkg, installed_version | version, fixed_version | fixed,
    severity, cvss | cvss_score, summary | title | description,
    references | refs | url                 (space/';'/',' separated)

CycloneDX VEX — ``vulnerabilities[]`` with ``affects[].ref`` resolved through
``components[]`` (bom-ref -> name/version, or a purl). Host: a metadata property
named host_id / hostname / fqdn / ip, else ``metadata.component.name``. An
``analysis.state`` of not_affected / false_positive / resolved is not a finding.

SARIF — every run's results whose rule id (or properties / message) names a
CVE. Host: ``invocations[].machine`` or run properties hostname / ip / host_id.
Package details come from result properties or a Trivy-style message
("Package: x / Installed Version: y / Fixed Version: z").
"""

from __future__ import annotations

import csv
import re

from libs.pluginbase import HostRef, ScanParseError, ScanRecord, VulnScanner

from ...jsonstream import JsonStreamError, Lazy
from ...refs import safe_urls
from ...risk import clean_cvss
from ...scanutil import clip, cve, find_cves, open_text, sniff

MAX_RECORDS = 500000
_SPLIT = re.compile(r"[\s;,|]+")
_LINK = re.compile(r"\((\w+:[^)\s]+)\)")

_CSV_COLS = {
    "host_id": ("host_id", "hostid", "asset_id"),
    "hostname": ("host", "hostname", "host_name", "asset", "name"),
    "fqdn": ("fqdn", "dns", "dns_name"),
    "ip": ("ip", "ip_address", "ipaddress", "address"),
    "cve": ("cve", "cve_id", "cveid", "vulnerability", "vulnerability_id", "vuln_id"),
    "package": ("package", "pkg", "package_name", "component"),
    "installed_version": ("installed_version", "version", "installed"),
    "fixed_version": ("fixed_version", "fixed", "fixed_in", "fix_version"),
    "severity": ("severity", "risk", "level"),
    "cvss": ("cvss", "cvss_score", "cvss3", "cvss_v3", "score"),
    "summary": ("summary", "title", "description"),
    "references": ("references", "refs", "reference", "url", "urls", "link"),
}
_NOT_A_FINDING = {"not_affected", "false_positive", "resolved", "resolved_with_pedigree"}


def _purl(ref: str) -> tuple[str, str]:
    """'pkg:deb/debian/openssl@3.0.11-1?arch=amd64' -> ('openssl', '3.0.11-1')."""
    if not ref.startswith("pkg:"):
        return "", ""
    body = ref[4:].split("?", 1)[0].split("#", 1)[0]
    path, _, ver = body.partition("@")
    name = path.rsplit("/", 1)[-1]
    from urllib.parse import unquote
    return unquote(name)[:255], unquote(ver)[:255]


def _host_from_props(props) -> HostRef:
    h = HostRef()
    for p in props or []:
        if not isinstance(p, dict):
            continue
        n = str(p.get("name") or "").lower().rsplit(":", 1)[-1]
        v = clip(p.get("value"), 255)
        if n in ("host_id", "hostid") and not h.host_id:
            h.host_id = v[:36]
        elif n in ("hostname", "host") and not h.hostname:
            h.hostname = v
        elif n == "fqdn" and not h.fqdn:
            h.fqdn = v
        elif n in ("ip", "ip_address") and not h.ip:
            h.ip = v[:64]
    return h


class GenericScanner(VulnScanner):
    """Imports CSV, CycloneDX VEX JSON or SARIF results from any scanner."""

    name = "generic"
    kind = "plugin"
    accepts_upload = True
    formats = ("csv", "cyclonedx-vex", "sarif")

    def parse(self, path: str):
        head = sniff(path)
        if head.startswith("{"):
            yield from self._json(path)
        elif head:
            yield from self._csv(path)
        else:
            raise ScanParseError("empty file")

    # ── CSV ──────────────────────────────────────────────────────────────────
    def _csv(self, path: str):
        try:
            with open_text(path) as fp:
                reader = csv.reader(fp)
                header = next(reader, None)
                if not header:
                    raise ScanParseError("CSV has no header row")
                norm = [h.strip().lower().replace(" ", "_").replace("-", "_") for h in header]
                ix: dict[str, int] = {}
                for field, aliases in _CSV_COLS.items():
                    for a in aliases:
                        if a in norm and field not in ix:
                            ix[field] = norm.index(a)
                if "cve" not in ix:
                    raise ScanParseError("CSV header needs a 'cve' column (cve, cve_id or vulnerability)")

                def col(row, field):
                    i = ix.get(field)
                    return row[i].strip() if i is not None and i < len(row) else ""

                n = 0
                for lineno, row in enumerate(reader, 2):
                    if not row or not "".join(row).strip():
                        continue
                    n += 1
                    if n > MAX_RECORDS:
                        raise ScanParseError(f"more than {MAX_RECORDS} rows")
                    c = cve(col(row, "cve"))
                    if not c:
                        raise ScanParseError(f"line {lineno}: '{col(row, 'cve')[:40]}' is not a CVE id")
                    raw_cvss = col(row, "cvss")
                    cvss = clean_cvss(raw_cvss) if raw_cvss else None
                    if raw_cvss and cvss is None:
                        raise ScanParseError(f"line {lineno}: cvss '{raw_cvss[:20]}' is not a number 0-10")
                    yield ScanRecord(
                        host=HostRef(host_id=col(row, "host_id")[:36], hostname=col(row, "hostname")[:255],
                                     fqdn=col(row, "fqdn")[:255], ip=col(row, "ip")[:64]),
                        cve_id=c, package=col(row, "package")[:255],
                        installed_version=col(row, "installed_version")[:255],
                        fixed_version=col(row, "fixed_version")[:255] or None,
                        severity=col(row, "severity")[:32], cvss=cvss,
                        summary=col(row, "summary")[:1000] or None,
                        references=safe_urls(_SPLIT.split(col(row, "references"))),
                    )
        except csv.Error as exc:
            raise ScanParseError(f"malformed CSV: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise ScanParseError("CSV is not valid UTF-8") from exc

    # ── JSON: CycloneDX or SARIF ─────────────────────────────────────────────
    def _json(self, path: str):
        components: dict[str, tuple[str, str]] = {}
        meta_host = HostRef()
        cdx_vulns: list[tuple] = []
        sarif: list[ScanRecord] = []
        bom_format = ""
        saw_runs = saw_vulns = False
        try:
            with open_text(path) as fp:
                lz = Lazy(fp)
                for key in lz.members():
                    if key == "bomFormat":
                        bom_format = str(lz.value())
                    elif key == "metadata":
                        meta = lz.value() or {}
                        if isinstance(meta, dict):
                            meta_host = _host_from_props(meta.get("properties"))
                            comp = meta.get("component") or {}
                            if not (meta_host.hostname or meta_host.host_id) and isinstance(comp, dict):
                                meta_host.hostname = clip(comp.get("name"), 255)
                    elif key == "components":
                        if lz.peek() != "[":
                            lz.skip()
                            continue
                        for _ in lz.items():
                            self._component(lz.value(), components)
                    elif key == "vulnerabilities":
                        saw_vulns = True
                        if lz.peek() != "[":
                            lz.skip()
                            continue
                        for _ in lz.items():
                            v = lz.value()
                            if isinstance(v, dict):
                                cdx_vulns.append(self._cdx_vuln(v))
                                if len(cdx_vulns) > MAX_RECORDS:
                                    raise ScanParseError(f"more than {MAX_RECORDS} vulnerabilities")
                    elif key == "runs":
                        saw_runs = True
                        if lz.peek() != "[":
                            lz.skip()
                            continue
                        for _ in lz.items():
                            sarif += self._sarif_run(lz)
                            if len(sarif) > MAX_RECORDS:
                                raise ScanParseError(f"more than {MAX_RECORDS} results")
                    else:
                        lz.skip()
        except JsonStreamError as exc:
            raise ScanParseError(f"invalid JSON: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise ScanParseError("file is not valid UTF-8") from exc

        if saw_runs:
            yield from sarif
            return
        if not (saw_vulns or bom_format.lower() == "cyclonedx"):
            raise ScanParseError("JSON is neither CycloneDX (bomFormat / vulnerabilities) nor SARIF (runs)")
        for cves, state, refs_aff, severity, cvss, summary, refs in cdx_vulns:
            if state in _NOT_A_FINDING:
                continue
            targets = []
            for ref in refs_aff or [""]:
                name, ver = components.get(ref) or _purl(ref)
                targets.append((name, ver))
            for c in cves:
                for name, ver in targets:
                    yield ScanRecord(host=meta_host, cve_id=c, package=name, installed_version=ver,
                                     fixed_version=None, severity=severity, cvss=cvss,
                                     summary=summary, references=refs)

    @staticmethod
    def _component(c, out: dict) -> None:
        if not isinstance(c, dict):
            return
        ref = str(c.get("bom-ref") or c.get("purl") or "")
        if ref:
            out[ref] = (clip(c.get("name"), 255), clip(c.get("version"), 255))
        for sub in c.get("components") or []:
            GenericScanner._component(sub, out)

    @staticmethod
    def _cdx_vuln(v: dict) -> tuple:
        cves = []
        for cand in [v.get("id"), *[(r or {}).get("id") for r in v.get("references") or [] if isinstance(r, dict)]]:
            c = cve(cand)
            if c and c not in cves:
                cves.append(c)
        cvss, severity = None, ""
        for r in v.get("ratings") or []:
            if not isinstance(r, dict):
                continue
            s = clean_cvss(r.get("score"))
            if s is not None and str(r.get("method") or "").upper().startswith("CVSS") and (cvss is None or s > cvss):
                cvss = s
            if not severity and r.get("severity"):
                severity = clip(r.get("severity"), 32)
        refs = [(v.get("source") or {}).get("url") if isinstance(v.get("source"), dict) else None]
        refs += [(a or {}).get("url") for a in v.get("advisories") or [] if isinstance(a, dict)]
        refs += [((r or {}).get("source") or {}).get("url") for r in v.get("references") or []
                 if isinstance(r, dict) and isinstance(r.get("source"), dict)]
        aff = [str((a or {}).get("ref") or "") for a in v.get("affects") or [] if isinstance(a, dict)]
        state = str(((v.get("analysis") or {}) if isinstance(v.get("analysis"), dict) else {}).get("state") or "").lower()
        summary = clip(v.get("description") or v.get("detail") or "", 1000) or None
        return (cves, state, [a for a in aff if a], severity, cvss, summary, safe_urls(refs))

    def _sarif_run(self, lz: Lazy) -> list[ScanRecord]:
        if lz.peek() != "{":
            lz.skip()
            return []
        rules: dict[str, dict] = {}
        host = HostRef()
        results: list[dict] = []
        for key in lz.members():
            if key == "tool":
                tool = lz.value() or {}
                driver = (tool.get("driver") or {}) if isinstance(tool, dict) else {}
                for r in driver.get("rules") or []:
                    if isinstance(r, dict) and r.get("id"):
                        rules[str(r["id"])] = r
            elif key == "invocations":
                for inv in lz.value() or []:
                    if isinstance(inv, dict) and inv.get("machine") and not host.hostname:
                        host.hostname = clip(inv["machine"], 255)
            elif key == "properties":
                props = lz.value() or {}
                if isinstance(props, dict):
                    host.host_id = host.host_id or clip(props.get("host_id"), 36)
                    host.hostname = host.hostname or clip(props.get("hostname") or props.get("host"), 255)
                    host.fqdn = host.fqdn or clip(props.get("fqdn"), 255)
                    host.ip = host.ip or clip(props.get("ip"), 64)
            elif key == "results":
                if lz.peek() != "[":
                    lz.skip()
                    continue
                for _ in lz.items():
                    r = lz.value()
                    if isinstance(r, dict):
                        results.append(self._sarif_compact(r))
            else:
                lz.skip()
        out = []
        for r in results:
            rule = rules.get(r["rule"]) or {}
            rprops = rule.get("properties") or {}
            cves = r["cves"] or [c for c in [cve(rule.get("id"))] if c] \
                or [c for t in (rprops.get("tags") or []) for c in [cve(t)] if c]
            if not cves:
                continue
            cvss = r["cvss"] if r["cvss"] is not None else clean_cvss(rprops.get("security-severity"))
            desc = rule.get("shortDescription") or {}
            summary = r["summary"] or (clip(desc.get("text"), 1000) if isinstance(desc, dict) else None) or None
            refs = safe_urls(r["refs"] + [rule.get("helpUri")])
            for c in cves:
                out.append(ScanRecord(host=host, cve_id=c, package=r["package"],
                                      installed_version=r["installed"], fixed_version=r["fixed"],
                                      severity=r["severity"], cvss=cvss, summary=summary, references=refs))
        return out

    @staticmethod
    def _sarif_compact(r: dict) -> dict:
        props = r.get("properties") if isinstance(r.get("properties"), dict) else {}
        msg = (r.get("message") or {}).get("text") if isinstance(r.get("message"), dict) else ""
        msg = str(msg or "")
        kv = {}
        for line in msg.splitlines():
            k, sep, v = line.partition(":")
            if sep:
                kv[k.strip().lower()] = v.strip()
        rule = str(r.get("ruleId") or (r.get("rule") or {}).get("id") or "")
        cves = [c for c in [cve(rule), cve(props.get("cve")), cve(kv.get("vulnerability"))] if c]
        if not cves:
            cves = find_cves(msg)[:5]
        pkg = props.get("package") or props.get("pkgName") or props.get("PkgName") or kv.get("package") or ""
        uri = ""
        for loc in r.get("locations") or []:
            try:
                uri = loc["physicalLocation"]["artifactLocation"]["uri"]
                break
            except (KeyError, TypeError):
                continue
        pname, pver = _purl(str(uri or ""))
        return {
            "rule": rule,
            "cves": list(dict.fromkeys(cves)),
            "package": clip(pkg or pname, 255),
            "installed": clip(props.get("installedVersion") or props.get("installed_version")
                              or kv.get("installed version") or pver, 255),
            "fixed": clip(props.get("fixedVersion") or props.get("fixed_version") or kv.get("fixed version"), 255) or None,
            "severity": clip(props.get("severity") or kv.get("severity") or r.get("level") or "", 32),
            "cvss": clean_cvss(props.get("cvss")) if props.get("cvss") is not None else None,
            "summary": clip(props.get("summary") or "", 1000) or None,
            "refs": [kv.get("link")] + _LINK.findall(msg) + list(props.get("references") or []),
        }
