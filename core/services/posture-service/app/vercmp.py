"""Package version comparison exactly as dpkg and rpm do it. Stdlib only.

The native matcher decides "installed < fixed" with these, so a wrong answer is
a missed vulnerability or a false one. Both algorithms are ported line for line
from their C originals (dpkg lib/dpkg/version.c ``verrevcmp`` and rpm
rpmio/rpmvercmp.c ``rpmvercmp``), including the parts that make naive string or
"natural" sorting wrong: dpkg's '~' sorts before everything (even the end of the
string) and letters sort before other symbols; rpm compares alternating
numeric/alpha segments, numeric beats alpha, '~' is a pre-release and '^' a
post-release marker.

Every function returns <0, 0 or >0 like strcmp.
"""

from __future__ import annotations

_DIGITS = "0123456789"


def _isdigit(c: str) -> bool:
    return c in _DIGITS and c != ""


def _isalpha(c: str) -> bool:
    return ("a" <= c <= "z") or ("A" <= c <= "Z")


# ── dpkg ──────────────────────────────────────────────────────────────────────

def _dpkg_order(c: str) -> int:
    if c == "" or _isdigit(c):
        return 0
    if _isalpha(c):
        return ord(c)
    if c == "~":
        return -1
    return ord(c) + 256


def _verrevcmp(a: str, b: str) -> int:
    i = j = 0
    la, lb = len(a), len(b)
    while i < la or j < lb:
        first_diff = 0
        while (i < la and not _isdigit(a[i])) or (j < lb and not _isdigit(b[j])):
            ac = _dpkg_order(a[i] if i < la else "")
            bc = _dpkg_order(b[j] if j < lb else "")
            if ac != bc:
                return ac - bc
            i += 1
            j += 1
        while i < la and a[i] == "0":
            i += 1
        while j < lb and b[j] == "0":
            j += 1
        while i < la and _isdigit(a[i]) and j < lb and _isdigit(b[j]):
            if not first_diff:
                first_diff = ord(a[i]) - ord(b[j])
            i += 1
            j += 1
        if i < la and _isdigit(a[i]):
            return 1
        if j < lb and _isdigit(b[j]):
            return -1
        if first_diff:
            return first_diff
    return 0


def parse_dpkg(v: str) -> tuple[int, str, str]:
    """'[epoch:]upstream[-revision]' -> (epoch, upstream, revision)."""
    v = (v or "").strip()
    epoch = 0
    if ":" in v:
        e, rest = v.split(":", 1)
        if e.isdigit():
            epoch, v = int(e), rest
    if "-" in v:
        up, rev = v.rsplit("-", 1)
    else:
        up, rev = v, ""
    return epoch, up, rev


def dpkg_compare(a: str, b: str) -> int:
    ea, ua, ra = parse_dpkg(a)
    eb, ub, rb = parse_dpkg(b)
    if ea != eb:
        return 1 if ea > eb else -1
    r = _verrevcmp(ua, ub)
    if r:
        return r
    return _verrevcmp(ra, rb)


# ── rpm ───────────────────────────────────────────────────────────────────────

def _isalnum(c: str) -> bool:
    return _isdigit(c) or _isalpha(c)


def rpmvercmp(a: str, b: str) -> int:
    if a == b:
        return 0
    one, two = a, b
    i = j = 0
    l1, l2 = len(one), len(two)
    while i < l1 or j < l2:
        while i < l1 and not _isalnum(one[i]) and one[i] not in "~^":
            i += 1
        while j < l2 and not _isalnum(two[j]) and two[j] not in "~^":
            j += 1
        c1 = one[i] if i < l1 else ""
        c2 = two[j] if j < l2 else ""
        # '~' sorts before anything, including the end of the string.
        if c1 == "~" or c2 == "~":
            if c1 != "~":
                return 1
            if c2 != "~":
                return -1
            i += 1
            j += 1
            continue
        # '^' sorts after the end of the string but before anything else.
        if c1 == "^" or c2 == "^":
            if not c1:
                return -1
            if not c2:
                return 1
            if c1 != "^":
                return 1
            if c2 != "^":
                return -1
            i += 1
            j += 1
            continue
        if not (c1 and c2):
            break
        s1, s2 = i, j
        if _isdigit(c1):
            while i < l1 and _isdigit(one[i]):
                i += 1
            while j < l2 and _isdigit(two[j]):
                j += 1
            isnum = True
        else:
            while i < l1 and _isalpha(one[i]):
                i += 1
            while j < l2 and _isalpha(two[j]):
                j += 1
            isnum = False
        seg1, seg2 = one[s1:i], two[s2:j]
        if not seg1:
            return -1  # unreachable in the C original, kept for safety
        if not seg2:
            # different segment types: numeric is newer than alpha
            return 1 if isnum else -1
        if isnum:
            seg1, seg2 = seg1.lstrip("0"), seg2.lstrip("0")
            if len(seg1) != len(seg2):
                return 1 if len(seg1) > len(seg2) else -1
        if seg1 != seg2:
            return 1 if seg1 > seg2 else -1
    if i >= l1 and j >= l2:
        return 0
    return -1 if i >= l1 else 1


def parse_evr(v: str) -> tuple[int, str, str]:
    """'[epoch:]version[-release]' -> (epoch, version, release). A missing epoch
    is 0, as modern rpm treats it."""
    v = (v or "").strip()
    epoch = 0
    if ":" in v:
        e, rest = v.split(":", 1)
        if e.isdigit():
            epoch, v = int(e), rest
    if "-" in v:
        ver, rel = v.rsplit("-", 1)
    else:
        ver, rel = v, ""
    return epoch, ver, rel


def rpm_compare(a: str, b: str) -> int:
    ea, va, ra = parse_evr(a)
    eb, vb, rb = parse_evr(b)
    if ea != eb:
        return 1 if ea > eb else -1
    r = rpmvercmp(va, vb)
    if r:
        return r
    # rpm only compares releases when both sides carry one.
    if ra and rb:
        return rpmvercmp(ra, rb)
    return 0


# ── dispatch ─────────────────────────────────────────────────────────────────

def compare(scheme: str, a: str, b: str) -> int:
    """scheme: deb | rpm. Anything else falls back to rpm's segment compare,
    which is the better general-purpose order of the two."""
    if scheme == "deb":
        return dpkg_compare(a, b)
    return rpm_compare(a, b)


def _zero(v: str | None) -> bool:
    return v is None or v.strip() in ("", "0")


def in_range(scheme: str, installed: str, introduced: str | None, fixed: str | None,
             last_affected: str | None) -> bool:
    """OSV range semantics: affected when introduced <= installed and
    (installed < fixed, or installed <= last_affected, or neither bound given)."""
    if not installed:
        return False
    if not _zero(introduced) and compare(scheme, installed, introduced) < 0:
        return False
    if fixed:
        return compare(scheme, installed, fixed) < 0
    if last_affected:
        return compare(scheme, installed, last_affected) <= 0
    return True
