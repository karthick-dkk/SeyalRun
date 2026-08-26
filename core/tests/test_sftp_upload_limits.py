"""The SFTP upload size limit has to agree in three places, or uploads break in a
way that a small test file hides.

Regression: /qa on 2026-08-26. Report: .gstack/qa-reports/qa-report-192-168-64-7-2026-08-26.md

The bug that prompted this: terminal-service advertised a 1 GiB upload limit, but
the edge nginx had no ``client_max_body_size``, so its 1 MiB default rejected any
real file with a raw HTML 413 before the request reached the app. A 44-byte test
file fit under 1 MiB, so nothing caught it.

Three numbers must line up:

  * the edge nginx cap must be >= the app's limit, or nginx rejects legitimate
    uploads the app would accept (the shipped bug);
  * the browser's fail-fast guard must EQUAL the app's limit — stricter would
    refuse files the server allows, looser would stream a gigabyte only to be
    rejected at the end;
  * the app limit is the source of truth the other two are checked against.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SFTP = ROOT / "services/terminal-service/app/api/sftp.py"
EDGE = ROOT / "services/edge-proxy/templates/default.conf.template"
PANEL = ROOT / "services/frontend/src/components/terminal/TermFilePanel.vue"

_GIB = 1024 ** 3


def _eval_intexpr(expr: str) -> int:
    """Safely evaluate a constant integer arithmetic string like ``1024 * 1024``
    or ``1024 ** 3`` — no eval(): only int literals and * / ** nodes are allowed,
    anything else raises."""
    def walk(node: ast.AST) -> int:
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Pow)):
            a, b = walk(node.left), walk(node.right)
            return a * b if isinstance(node.op, ast.Mult) else a ** b
        raise ValueError(f"unexpected node in size expression: {ast.dump(node)}")
    return walk(ast.parse(expr.strip(), mode="eval").body)


def _code_only(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(
        ln for ln in text.splitlines()
        if not ln.lstrip().startswith(("//", "#"))
    )


def _backend_limit() -> int:
    m = re.search(r"MAX_TRANSFER_BYTES\s*=\s*([0-9*\s]+)", SFTP.read_text())
    assert m, "MAX_TRANSFER_BYTES not found"
    return _eval_intexpr(m.group(1))


def _frontend_guard() -> int:
    m = re.search(r"MAX_UPLOAD_BYTES\s*=\s*([0-9*\s]+(?:\*\*\s*[0-9]+)?)", PANEL.read_text())
    assert m, "MAX_UPLOAD_BYTES not found in the file panel"
    return _eval_intexpr(m.group(1))


def _edge_body_limit_bytes() -> int:
    m = re.search(r"client_max_body_size\s+(\d+)([kKmMgG]?);", _code_only(EDGE.read_text()))
    assert m, "client_max_body_size is missing from the edge config — nginx's 1 MiB default will cap uploads"
    n = int(m.group(1))
    unit = (m.group(2) or "").lower()
    return n * {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[unit]


def test_backend_limit_is_one_gib():
    assert _backend_limit() == _GIB, "the documented SFTP limit is 1 GiB"


def test_edge_allows_at_least_the_app_limit():
    edge = _edge_body_limit_bytes()
    assert edge >= _backend_limit(), (
        f"edge client_max_body_size ({edge} B) is below the app limit "
        f"({_backend_limit()} B) — nginx will 413 uploads the app would accept"
    )
    # scoped to the /api/ location (where uploads go), not disabled globally.
    # Slice the block by hand: the location body contains ${INTERNAL_PROTO}, whose
    # brace defeats a naive [^}]* regex.
    conf = EDGE.read_text()
    start = conf.index("location /api/")
    nxt = conf.find("location ", start + 1)
    api_block = conf[start: nxt if nxt != -1 else len(conf)]
    assert "client_max_body_size" in api_block, \
        "client_max_body_size must live in the /api/ location block"


def test_frontend_guard_equals_backend_limit():
    assert _frontend_guard() == _backend_limit(), (
        "the browser's fail-fast guard must match the server limit exactly — "
        "not stricter (refusing allowed files), not looser (uploading then failing)"
    )


# ── the guard actually short-circuits before the upload, and drop shares it ──

def test_uploadfile_refuses_oversize_before_posting():
    code = _code_only(PANEL.read_text())
    fn = code[code.index("function uploadFile"):]
    fn = fn[: fn.index("\nasync function", 1) if "\nasync function" in fn[1:] else fn.index("\nfunction", 1)]
    guard = fn.index("MAX_UPLOAD_BYTES")
    post = fn.index("api.post")
    ret = fn.index("return", guard)
    assert guard < ret < post, "the size check + early return must come BEFORE api.post"


def test_drop_and_picker_share_one_upload_path():
    code = _code_only(PANEL.read_text())
    # both entry points funnel through uploadFile(), so both get the guard + jail
    assert "await uploadFile(file)" in code
    assert code.count("uploadFile(file)") >= 2, "onUpload and onDrop must both call uploadFile"
    # drop reads the OS file off the dataTransfer (a real cross-app file), and the
    # template wires the handlers
    assert "dataTransfer?.files" in code
    tpl = PANEL.read_text()
    assert "@drop.prevent=\"onDrop\"" in tpl and "@dragover.prevent=\"onDragOver\"" in tpl


def test_upload_destination_is_the_fixed_root_not_the_browsed_dir():
    """A dropped/picked file always lands in the SFTP root, matching the server's
    upload_target() pin — sending the browsed cwd would just 403."""
    code = _code_only(PANEL.read_text())
    fn = code[code.index("function uploadFile"): code.index("function uploadFile") + 600]
    assert "fd.append('path', DEFAULT_PATH)" in fn
