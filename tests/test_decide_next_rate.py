"""Unit tests for the pure decision logic in growatt_export_limiter.

These tests exercise decide_next_rate() in isolation — no network calls,
no environment, no Growatt cloud. They lock in the curtail/release
behaviour and the hysteresis margin so refactors don't silently change
when the inverter is throttled.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub modules the script imports at module scope, so the import doesn't
# require the real packages to be installed during test runs.
import types

if "growattServer" not in sys.modules:
    stub = types.ModuleType("growattServer")
    stub.OpenApiV1 = object  # type: ignore[attr-defined]
    sys.modules["growattServer"] = stub

if "requests" not in sys.modules:
    stub_req = types.ModuleType("requests")
    class _RE(Exception):
        pass
    stub_req.RequestException = _RE  # type: ignore[attr-defined]
    stub_req.post = lambda *a, **kw: None  # type: ignore[attr-defined]
    sys.modules["requests"] = stub_req

from growatt_export_limiter import decide_next_rate, FULL_PCT, CURTAILED_PCT  # noqa: E402

LIMIT = 8500
HYST = 200
RELEASE_THRESHOLD = (LIMIT - HYST) * 0.85  # 7055


class TestCurtailFromFull:
    def test_well_above_limit_curtails(self):
        assert decide_next_rate(10000, FULL_PCT, LIMIT, HYST) == CURTAILED_PCT

    def test_just_above_hysteresis_curtails(self):
        # limit + hysteresis = 8700; need strictly greater
        assert decide_next_rate(8701, FULL_PCT, LIMIT, HYST) == CURTAILED_PCT

    def test_at_hysteresis_boundary_holds(self):
        # 8700 is not strictly greater than 8700 -> stay at full
        assert decide_next_rate(8700, FULL_PCT, LIMIT, HYST) == FULL_PCT

    def test_at_limit_holds(self):
        assert decide_next_rate(LIMIT, FULL_PCT, LIMIT, HYST) == FULL_PCT

    def test_below_limit_holds(self):
        assert decide_next_rate(5000, FULL_PCT, LIMIT, HYST) == FULL_PCT


class TestReleaseFromCurtailed:
    def test_well_below_threshold_releases(self):
        # threshold = 7055; 5000 is well below
        assert decide_next_rate(5000, CURTAILED_PCT, LIMIT, HYST) == FULL_PCT

    def test_just_below_threshold_releases(self):
        assert decide_next_rate(7054, CURTAILED_PCT, LIMIT, HYST) == FULL_PCT

    def test_at_threshold_holds(self):
        # need strictly less than threshold
        assert decide_next_rate(RELEASE_THRESHOLD, CURTAILED_PCT, LIMIT, HYST) == CURTAILED_PCT

    def test_above_threshold_holds_curtailed(self):
        # 7500 > 7055; stay curtailed even though we're under the raw limit,
        # because the inverter is currently capped at 50 % anyway
        assert decide_next_rate(7500, CURTAILED_PCT, LIMIT, HYST) == CURTAILED_PCT

    def test_at_full_ceiling_holds_curtailed(self):
        # output sitting at the 50 % ceiling means PV could still be over
        assert decide_next_rate(7500, CURTAILED_PCT, LIMIT, HYST) == CURTAILED_PCT


class TestNoChangeCases:
    def test_zero_power_at_full_holds(self):
        assert decide_next_rate(0, FULL_PCT, LIMIT, HYST) == FULL_PCT

    def test_zero_power_at_curtailed_releases(self):
        assert decide_next_rate(0, CURTAILED_PCT, LIMIT, HYST) == FULL_PCT


class TestParameterised:
    @pytest.mark.parametrize("power,current,expected", [
        (10000, FULL_PCT, CURTAILED_PCT),       # over -> curtail
        (8500,  FULL_PCT, FULL_PCT),            # at limit -> hold
        (3000,  FULL_PCT, FULL_PCT),            # low -> hold
        (3000,  CURTAILED_PCT, FULL_PCT),       # low while curtailed -> release
        (8000,  CURTAILED_PCT, CURTAILED_PCT),  # near limit while curtailed -> hold
    ])
    def test_matrix(self, power, current, expected):
        assert decide_next_rate(power, current, LIMIT, HYST) == expected
