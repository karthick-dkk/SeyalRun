"""posture M2: dpkg and rpm version comparison, as the native matcher uses it.

Vectors are the cases that break naive or "natural" ordering — dpkg's '~'
before everything and letters before symbols; rpm's alpha/numeric segments,
'~' pre-releases and '^' post-releases — most taken from the dpkg and rpm test
suites, plus real distro versions from the feed fixtures.
"""

from __future__ import annotations

import pytest

VERCMP = "services/posture-service/app/vercmp.py"


@pytest.fixture
def vc(load_module):
    return load_module(VERCMP, "_posture_vercmp")


def _sign(n: int) -> int:
    return (n > 0) - (n < 0)


@pytest.mark.parametrize("a,b,want", [
    ("1.0", "1.0", 0),
    ("1.0", "1.0-0", 0),            # missing revision == "0"
    ("1.0~rc1", "1.0", -1),         # tilde sorts before the end of the string
    ("1.0~~", "1.0~", -1),
    ("1.0~", "1.0~a", -1),
    ("1.0", "1.0+b1", -1),
    ("1.0a", "1.0+", -1),           # letters sort before non-letters
    ("1.2.3", "1.2.3a", -1),
    ("2.30-1", "2.4-1", 1),         # numeric, not lexical
    ("1:1.0", "2.0", 1),            # epoch dominates
    ("0:1.0", "1.0", 0),
    ("1.0-1", "1.0-2", -1),
    ("1.0-1ubuntu1", "1.0-1", 1),
    ("3.0.11-1~deb12u2", "3.0.13-1~deb12u1", -1),
    ("3.0.13-1~deb12u1", "3.0.13-1", -1),
    ("3.7.9-2+deb12u1", "3.7.9-2+deb12u2", -1),
    ("1.2.13.dfsg-1", "1.2.13-1", 1),
    ("007", "7", 0),                # leading zeros
])
def test_dpkg_compare(vc, a, b, want):
    assert _sign(vc.dpkg_compare(a, b)) == want
    assert _sign(vc.dpkg_compare(b, a)) == -want


@pytest.mark.parametrize("a,b,want", [
    ("1.0", "1.0", 0),
    ("1.0", "2.0", -1),
    ("2.0.1", "2.0.1a", -1),
    ("5.5p1", "5.5p2", -1),
    ("5.5p10", "5.5p1", 1),
    ("10xyz", "10.1xyz", -1),
    ("xyz10", "xyz10.1", -1),
    ("1.0aa", "1.0a", 1),
    ("a", "1", -1),                 # numeric segment beats alpha
    ("1b.fc17", "1.fc17", -1),
    ("6.0.rc1", "6.0", 1),
    ("1.0~rc1", "1.0", -1),
    ("1.0~rc1", "1.0~rc2", -1),
    ("1.0~rc1~git123", "1.0~rc1", -1),
    ("1.0^", "1.0", 1),
    ("1.0^git1", "1.0", 1),
    ("1.0^git1", "1.01", -1),
    ("1.0^20160101", "1.0.1", -1),
    ("1.0~rc1^git1", "1.0~rc1", 1),
    ("1.0^git1~pre", "1.0^git1", -1),
    ("1.0.0", "1.0", 1),
    ("1..0", "1.0", 0),             # separators are not segments
    ("01", "1", 0),
])
def test_rpmvercmp(vc, a, b, want):
    assert _sign(vc.rpmvercmp(a, b)) == want
    assert _sign(vc.rpmvercmp(b, a)) == -want


@pytest.mark.parametrize("a,b,want", [
    ("1:3.0.7-24.el9", "1:3.0.7-25.el9_3", -1),
    ("3.0.7-25.el9_3", "1:3.0.7-25.el9_3", -1),   # missing epoch is 0
    ("2.34-100.el9", "2.34-100.el9_4.2", -1),
    ("0:2.34-100.el9_4.2", "2.34-100.el9_4.2", 0),
    ("2.34-100.el9_4.2", "2.34-100.el9_4.10", -1),
    ("2.34", "2.34-100.el9", 0),                  # release only compared when both have one
])
def test_rpm_evr_compare(vc, a, b, want):
    assert _sign(vc.rpm_compare(a, b)) == want


def test_in_range_follows_osv_semantics(vc):
    assert vc.in_range("deb", "3.0.11-1~deb12u2", "0", "3.0.13-1~deb12u1", None)
    assert not vc.in_range("deb", "3.0.13-1~deb12u1", "0", "3.0.13-1~deb12u1", None)
    assert not vc.in_range("deb", "1.0", "2.0", "3.0", None)          # before introduced
    assert vc.in_range("rpm", "1.5-1", "1.0", None, "1.5-1")          # last_affected is inclusive
    assert not vc.in_range("rpm", "1.6-1", "1.0", None, "1.5-1")
    assert vc.in_range("rpm", "9.9", "0", None, None)                 # open-ended range
    assert not vc.in_range("rpm", "", "0", "1.0", None)               # no installed version: no claim


def test_scheme_dispatch(vc):
    # dpkg and rpm disagree on '+': dpkg sorts letters before symbols.
    assert vc.compare("deb", "1.0a", "1.0+") < 0
    assert vc.compare("rpm", "1.0a", "1.0+") > 0
