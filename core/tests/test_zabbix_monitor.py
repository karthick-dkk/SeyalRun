"""Agentless Zabbix monitor: health classification and Prometheus parsing must
keep the exact semantics of the retired zabbix-agent2 scripts (health.sh /
metrics.sh) — "down" on any non-200, JSON "status" preferred over body text,
and clean zeros (never errors) for missing metrics. Pure/dependency-free."""

from __future__ import annotations

import pytest


@pytest.fixture
def mon(load_module):
    return load_module(
        "services/zabbix-integration-service/app/monitorlib.py", "za_monitorlib"
    )


class TestClassifyHealth:
    def test_json_status_ok(self, mon):
        assert mon.classify_health(200, '{"status": "ok", "db": true}') == "ok"

    def test_json_status_degraded(self, mon):
        assert mon.classify_health(200, '{"status": "degraded", "db": false}') == "degraded"

    def test_non_200_is_down(self, mon):
        assert mon.classify_health(500, '{"status": "ok"}') == "down"
        assert mon.classify_health(None, "") == "down"

    def test_plaintext_body_passthrough(self, mon):
        # edge-proxy /health returns bare "ok\n"
        assert mon.classify_health(200, "ok\n") == "ok"

    def test_quoted_and_padded_text(self, mon):
        assert mon.classify_health(200, ' "ok" \n') == "ok"

    def test_json_without_status_falls_back_to_text(self, mon):
        assert mon.classify_health(200, '{"db": true}') == '{"db":true}'

    def test_empty_200_body_is_down(self, mon):
        assert mon.classify_health(200, "") == "down"

    def test_json_array_body_does_not_crash(self, mon):
        assert mon.classify_health(200, "[1, 2]") == "[1,2]"


class TestParseSeyalrunMetrics:
    SNAPSHOT = '{"requests_total": 1234, "errors_total": 5, "uptime_seconds": 987.5}'

    def test_extracts_all_three(self, mon):
        v = mon.parse_seyalrun_metrics(self.SNAPSHOT)
        assert v == {"requests_total": 1234.0, "errors_total": 5.0, "uptime_seconds": 987.5}

    def test_missing_keys_yield_zero(self, mon):
        v = mon.parse_seyalrun_metrics('{"requests_total": 7}')
        assert v == {"requests_total": 7.0, "errors_total": 0.0, "uptime_seconds": 0.0}

    def test_extra_keys_ignored(self, mon):
        # api-gateway adds "upstreams": {...} to its snapshot — the monitor
        # payload must stay the three flat keys the template JSONPaths expect.
        v = mon.parse_seyalrun_metrics(
            '{"requests_total": 3, "errors_total": 0, "uptime_seconds": 1.5, '
            '"upstreams": {"identity-service": {"p95_ms": 12}}}'
        )
        assert v == {"requests_total": 3.0, "errors_total": 0.0, "uptime_seconds": 1.5}

    def test_unparsable_bodies_yield_zeros(self, mon):
        # empty, non-JSON (old Prometheus text), or non-object JSON
        for body in ("", "seyalrun_requests_total 7\n", "[1,2]", "null"):
            assert set(mon.parse_seyalrun_metrics(body).values()) == {0.0}

    def test_non_numeric_and_bool_values_yield_zero(self, mon):
        v = mon.parse_seyalrun_metrics(
            '{"requests_total": "many", "errors_total": true, "uptime_seconds": null}'
        )
        assert v == {"requests_total": 0.0, "errors_total": 0.0, "uptime_seconds": 0.0}
