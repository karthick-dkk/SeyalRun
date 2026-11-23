"""Vuls JSON report parser (one server per report, as Vuls writes results/<date>/<server>.json).

Vuls is GPL and is never imported or executed here — only its result file is
read. The report is walked incrementally (app.jsonstream): ``scannedCves`` is
processed one CVE at a time and ``packages`` is reduced to name -> version as
it is read, so a large report does not have to fit in memory as Python objects.
"""

from __future__ import annotations

from libs.pluginbase import HostRef, ScanParseError, ScanRecord, VulnScanner

from ...jsonstream import JsonStreamError, Lazy
from ...refs import safe_urls
from ...risk import clean_cvss
from ...scanutil import clip, cve, open_text, sniff

MAX_CVES = 200000


def _contents(cc) -> list[dict]:
    """cveContents: {source: [content, ...]} (current) or {source: content} (old)."""
    out: list[dict] = []
    if not isinstance(cc, dict):
        return out
    # NVD first: its summary and CVSS are the vendor-neutral ones.
    for src in sorted(cc, key=lambda s: (s != "nvd", s)):
        val = cc[src]
        for c in (val if isinstance(val, list) else [val]):
            if isinstance(c, dict):
                out.append(c)
    return out


def _compact(cve_id: str, body: dict) -> list[tuple]:
    contents = _contents(body.get("cveContents"))
    cvss = None
    severity = ""
    summary = None
    refs: list[str] = []
    for c in contents:
        for key in ("cvss3Score", "cvss2Score"):
            v = clean_cvss(c.get(key))
            if v and (cvss is None or v > cvss):
                cvss = v
        if not severity:
            severity = clip(c.get("cvss3Severity") or c.get("cvss2Severity") or "", 32)
        if summary is None and (c.get("summary") or c.get("title")):
            summary = clip(c.get("summary") or c.get("title"), 1000)
        refs.append(c.get("sourceLink"))
        for r in c.get("references") or []:
            if isinstance(r, dict):
                refs.append(r.get("link"))
    refs = safe_urls(refs)
    pkgs = []
    for p in body.get("affectedPackages") or []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        fixed = None if p.get("notFixedYet") else (clip(p.get("fixedIn"), 255) or None)
        pkgs.append((clip(p["name"], 255), fixed))
    if not pkgs:
        pkgs = [("", None)]
    return [(cve_id, name, fixed, severity, cvss, summary, refs) for name, fixed in pkgs]


class VulsScanner(VulnScanner):
    """Imports a Vuls JSON scan report (scannedCves, affectedPackages, cveContents)."""

    name = "vuls"
    kind = "plugin"
    accepts_upload = True
    formats = ("vuls-json",)

    def parse(self, path: str):
        if not sniff(path).startswith("{"):
            raise ScanParseError("not a Vuls JSON report (expected a JSON object)")
        server = ""
        ips: list[str] = []
        packages: dict[str, str] = {}
        rows: list[tuple] = []
        seen_cves = False
        try:
            with open_text(path) as fp:
                lz = Lazy(fp)
                for key in lz.members():
                    if key == "serverName":
                        server = clip(lz.value(), 255)
                    elif key in ("ipv4Addrs", "ipv6Addrs"):
                        v = lz.value()
                        ips += [clip(x, 64) for x in (v or []) if isinstance(x, str)]
                    elif key == "scannedCves":
                        seen_cves = True
                        if lz.peek() == "n":   # null: nothing found
                            lz.value()
                            continue
                        n = 0
                        for cid in lz.members():
                            body = lz.value()
                            n += 1
                            if n > MAX_CVES:
                                raise ScanParseError(f"report lists more than {MAX_CVES} CVEs")
                            c = cve(cid) or cve((body or {}).get("cveID"))
                            if c and isinstance(body, dict):
                                rows += _compact(c, body)
                    elif key == "packages":
                        if lz.peek() != "{":
                            lz.skip()
                            continue
                        for pname in lz.members():
                            p = lz.value()
                            if isinstance(p, dict):
                                ver = clip(p.get("version"), 200)
                                rel = clip(p.get("release"), 200)
                                if p.get("epoch") not in (None, "", "0", 0):
                                    ver = f"{p['epoch']}:{ver}"
                                packages[clip(pname, 255)] = f"{ver}-{rel}" if rel else ver
                    else:
                        lz.skip()
        except JsonStreamError as exc:
            raise ScanParseError(f"invalid Vuls report: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise ScanParseError("Vuls report is not valid UTF-8") from exc
        if not seen_cves:
            raise ScanParseError("not a Vuls report: no 'scannedCves' member")
        host = HostRef(hostname=server, ip=next((i for i in ips if i and not i.startswith("127.")), ""))
        for cve_id, name, fixed, severity, cvss, summary, refs in rows:
            yield ScanRecord(host=host, cve_id=cve_id, package=name,
                             installed_version=packages.get(name, ""), fixed_version=fixed,
                             severity=severity, cvss=cvss, summary=summary, references=refs)
