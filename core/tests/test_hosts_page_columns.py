"""The Hosts table shows what an operator manages, not what the system is doing.

Requested change: drop Sessions and Users, add Groups, and label the topology
column Zone rather than Gateway.

Each has a reason worth keeping:

  * Zone, not Gateway — the zone is the thing an operator configures. Its gateway
    is a property of the zone, and a zone may now hold SEVERAL, so naming one of
    them in a per-host column would be actively misleading about the route.
  * Groups — group membership is how authorization is granted at scale, so
    showing it here is what makes "who can reach this host" answerable without
    opening each asset.
  * Sessions and Users were activity, not configuration; the Sessions page
    already owns the first and Authorizations the second.

Also pins that removing a column removed its data fetch. A column deleted from
the markup while its loader stays behind is a request made on every page load
for something nobody renders.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIEW = ROOT / "services/frontend/src/views/HostsView.vue"


def _template() -> str:
    src = VIEW.read_text()
    return src[src.find("<template>"): src.rfind("</template>")]


def test_view_parses():
    """Guard against every assertion below passing vacuously."""
    assert "<th" in _template()


def test_removed_columns_are_gone():
    t = _template()
    assert ">Sessions<" not in t
    assert ">Users<" not in t


def test_groups_column_exists():
    t = _template()
    assert ">Groups<" in t
    assert "groupNames(host)" in t, "the column must render real membership"


def test_topology_column_is_labelled_zone():
    t = _template()
    assert ">Zone<" in t
    assert ">Gateway<" not in t, (
        "a zone may hold several gateways — naming one of them per host "
        "misrepresents the route"
    )


def test_zone_column_shows_the_zone_not_a_gateway_name():
    t = _template()
    assert "zoneName(host.zone_id)" in t
    assert "zoneGateway(host.zone_id)!.name" not in t


def test_removing_the_column_removed_its_fetch():
    """A loader left behind after its column is deleted requests something nobody
    renders, on every page load."""
    src = VIEW.read_text()
    assert "sessionCounts" not in src, "the session-count state is dead"
    assert "hostUsers(" not in src, "the per-host user lookup is dead"
    # The remaining /ssh/sessions call belongs to the expanded row detail, which
    # is a different feature and is parameterised by host.
    calls = re.findall(r"api\.get\('/ssh/sessions'[^)]*\)", src)
    assert len(calls) == 1 and "host_id" in calls[0], calls


def test_groups_are_actually_loaded():
    """A column reading from an empty list renders "—" forever and looks like the
    hosts have no groups."""
    src = VIEW.read_text()
    assert "api.get('/host-groups')" in src
    assert "hostGroups.value = groupsR.data" in src
