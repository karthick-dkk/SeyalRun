"""OpenSCAP result parser: ARF, XCCDF TestResult and OVAL results XML.

OpenSCAP is GPL and is never imported or executed here — only the result file
an admin uploads (``oscap xccdf eval --results-arf``, ``--results`` or
``oscap oval eval --results``) is read.

What counts as a finding:
  * an OVAL definition whose result is "true" (vulnerable / patch missing),
    one record per CVE it references x package named in its criteria
    ("openssl is earlier than 1:3.0.7-25.el9_3"), and
  * an XCCDF rule-result "fail" carrying CVE idents; if it checks an OVAL
    definition found in the same file, that definition's packages are used.

The host is read from the XCCDF TestResult (target, target-address, the fqdn
target fact), the ARF asset (fqdn, hostname, ip) or the OVAL system_info
(primary_host_name, ip_address). One file describes one host; that is how
oscap writes them.

Streaming expat with DOCTYPE and entity declarations refused (no billion
laughs, no external entities), as for feed OVAL.
"""

from __future__ import annotations

import re
from xml.parsers import expat

from libs.pluginbase import HostRef, ScanParseError, ScanRecord, VulnScanner

from ...feedbundle import oval_criterion
from ...refs import safe_urls
from ...risk import clean_cvss
from ...scanutil import clip, cve, open_binary, sniff

_CHUNK = 1024 * 1024
_LEAD_FLOAT = re.compile(r"^\s*(\d+(?:\.\d+)?)")
_TEXT_TAGS = {"title", "severity", "target", "target-address", "fact", "fqdn", "hostname",
              "ip-address", "ip-v4", "ip-v6", "primary_host_name", "ip_address", "ident", "cve", "result"}


class OpenScapScanner(VulnScanner):
    """Imports OpenSCAP ARF / XCCDF / OVAL result XML (failed CVE checks)."""

    name = "openscap"
    kind = "plugin"
    accepts_upload = True
    formats = ("arf-xml", "xccdf-results-xml", "oval-results-xml")

    def parse(self, path: str):
        if not sniff(path).startswith("<"):
            raise ScanParseError("not an OpenSCAP result (expected XML)")
        defs: dict[str, dict] = {}
        def_results: dict[str, str] = {}
        rule_results: list[dict] = []
        host = HostRef()
        st: dict = {"def": None, "rr": None, "text": None, "tag": None, "attrs": None,
                    "in_results": False, "fact": None, "saw_scap": False}

        def deny(*_a):
            raise ScanParseError("DOCTYPE/entity declarations are not allowed in result files")

        def start(name, attrs):
            local = name.rsplit(" ", 1)[-1]
            if local in ("oval_definitions", "oval_results", "Benchmark", "TestResult", "asset-report-collection",
                         "data-stream-collection"):
                st["saw_scap"] = True
            if local == "results":
                st["in_results"] = True
            d, rr = st["def"], st["rr"]
            if local == "definition":
                if "definition_id" in attrs:          # oval_results/results/system/definitions
                    def_results[attrs["definition_id"]] = str(attrs.get("result", "")).lower()
                elif "id" in attrs and not st["in_results"]:
                    st["def"] = {"id": attrs["id"], "cves": {}, "pkgs": [], "refs": [], "title": None,
                                 "severity": "", "class": attrs.get("class", "")}
                return
            if local == "rule-result":
                st["rr"] = {"result": "", "cves": [], "oval": [], "severity": attrs.get("severity", "")}
                return
            if d is not None:
                if local == "reference":
                    if str(attrs.get("source", "")).upper() == "CVE":
                        c = cve(attrs.get("ref_id"))
                        if c:
                            d["cves"].setdefault(c, None)
                    d["refs"].append(attrs.get("ref_url"))
                elif local == "criterion":
                    hit = oval_criterion(attrs.get("comment", ""))
                    if hit and hit not in d["pkgs"]:
                        d["pkgs"].append(hit)
                elif local == "cve":
                    st["attrs"] = dict(attrs)
                    d["refs"].append(attrs.get("href"))
            if rr is not None and local == "check-content-ref" and attrs.get("name"):
                rr["oval"].append(attrs["name"])
            if local == "fact":
                st["fact"] = attrs.get("name", "")
            if local == "ident":
                st["attrs"] = dict(attrs)
            if local in _TEXT_TAGS:
                st["tag"], st["text"] = local, []

        def chars(data):
            if st["text"] is not None:
                st["text"].append(data)

        def end(name):
            local = name.rsplit(" ", 1)[-1]
            d, rr = st["def"], st["rr"]
            if st["tag"] == local and st["text"] is not None:
                val = "".join(st["text"]).strip()
                st["tag"], st["text"] = None, None
                if d is not None:
                    if local == "title" and d["title"] is None:
                        d["title"] = val[:1000]
                    elif local == "severity":
                        d["severity"] = val[:32]
                    elif local == "cve":
                        c = cve(val)
                        if c:
                            a = st["attrs"] or {}
                            m = _LEAD_FLOAT.match(str(a.get("cvss3") or a.get("cvss_score") or ""))
                            d["cves"][c] = clean_cvss(m.group(1)) if m else d["cves"].get(c)
                elif rr is not None:
                    if local == "ident":
                        c = cve(val)
                        if c and c not in rr["cves"]:
                            rr["cves"].append(c)
                if local == "result" and rr is not None:
                    rr["result"] = val.lower()
                if local in ("target", "hostname", "primary_host_name") and val and not host.hostname:
                    host.hostname = val[:255]
                elif local == "fqdn" and val and not host.fqdn:
                    host.fqdn = val[:255]
                elif local == "fact" and "fqdn" in (st["fact"] or "") and val and not host.fqdn:
                    host.fqdn = val[:255]
                elif local in ("target-address", "ip-address", "ip-v4", "ip_address") and val and not host.ip:
                    if not val.startswith(("127.", "::1", "0:0:0:0:0:0:0:1")):
                        host.ip = val[:64]
            if local == "definition" and d is not None:
                defs[d["id"]] = d
                st["def"] = None
            elif local == "rule-result" and rr is not None:
                if rr["result"] == "fail" and (rr["cves"] or rr["oval"]):
                    rule_results.append(rr)
                st["rr"] = None
            elif local == "results":
                st["in_results"] = False

        parser = expat.ParserCreate(namespace_separator=" ")
        parser.StartElementHandler = start
        parser.EndElementHandler = end
        parser.CharacterDataHandler = chars
        parser.StartDoctypeDeclHandler = deny
        parser.EntityDeclHandler = deny
        try:
            with open_binary(path) as f:
                while True:
                    chunk = f.read(_CHUNK)
                    parser.Parse(chunk, not chunk)
                    if not chunk:
                        break
        except expat.ExpatError as exc:
            raise ScanParseError(f"malformed XML ({exc})") from exc
        except (OSError, EOFError) as exc:
            raise ScanParseError(f"cannot read the uploaded file ({exc})") from exc
        if not st["saw_scap"]:
            raise ScanParseError("not an OpenSCAP result: no ARF, XCCDF or OVAL root element")

        seen: set[tuple[str, str]] = set()

        def emit(d: dict | None, cves: list[str], severity: str):
            pkgs = (d or {}).get("pkgs") or [("", None)]
            refs = safe_urls((d or {}).get("refs") or [])
            for c in cves:
                cvss = ((d or {}).get("cves") or {}).get(c)
                for pkg, fixed in pkgs:
                    if (c, pkg) in seen:
                        continue
                    seen.add((c, pkg))
                    yield ScanRecord(host=host, cve_id=c, package=clip(pkg, 255), installed_version="",
                                     fixed_version=(clip(fixed, 255) or None) if fixed else None,
                                     severity=(d or {}).get("severity") or severity, cvss=cvss,
                                     summary=(d or {}).get("title"), references=refs)

        for def_id, result in def_results.items():
            if result != "true":
                continue
            d = defs.get(def_id)
            if d and d["cves"]:
                yield from emit(d, list(d["cves"]), "")
        for rr in rule_results:
            linked = [defs[o] for o in rr["oval"] if o in defs]
            if linked:
                for d in linked:
                    yield from emit(d, rr["cves"] or list(d["cves"]), rr["severity"])
            elif rr["cves"]:
                yield from emit(None, rr["cves"], rr["severity"])
