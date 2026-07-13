"""Tests for jobstar.idle.resource_checker."""
import time
from unittest.mock import patch, MagicMock

import pytest

from jobstar.idle.resource_checker import (
    ResourceSample,
    ResourceChecker,
    AvailabilityDecision,
)


class TestResourceSample:
    def test_from_dict(self):
        d = {"cpu_percent": 12.5, "mem_percent": 45.0, "disk_percent": 60.0, "load_avg_1m": 0.3}
        s = ResourceSample.from_dict(d)
        assert s.cpu_percent == 12.5
        assert s.mem_percent == 45.0
        assert s.disk_percent == 60.0
        assert s.load_avg_1m == 0.3
        assert s.timestamp > 0

    def test_to_dict(self):
        s = ResourceSample(cpu_percent=10, mem_percent=20, disk_percent=30, load_avg_1m=0.1, timestamp=12345)
        d = s.to_dict()
        assert d["cpu_percent"] == 10
        assert d["timestamp"] == 12345

    def test_to_log_line(self):
        s = ResourceSample(cpu_percent=10, mem_percent=20, disk_percent=30, load_avg_1m=0.1, timestamp=12345)
        line = s.to_log_line()
        assert "cpu=10.0%" in line
        assert "mem=20.0%" in line


class TestResourceChecker:
    @pytest.fixture
    def thresholds(self):
        return {"cpu_max": 50.0, "mem_max": 70.0, "disk_max": 90.0, "load_max": 2.0}

    @pytest.fixture
    def checker(self, thresholds):
        return ResourceChecker(thresholds)

    def test_sample_collects_current_resources(self, checker):
        with patch("jobstar.idle.resource_checker.psutil") as mock_psutil:
            mock_psutil.cpu_percent.return_value = 15.0
            mock_psutil.virtual_memory.return_value = MagicMock(percent=40.0)
            mock_psutil.disk_usage.return_value = MagicMock(percent=55.0)
            mock_psutil.getloadavg.return_value = (0.2, 0.3, 0.4)

            sample = checker.sample()

        assert sample.cpu_percent == 15.0
        assert sample.mem_percent == 40.0
        assert sample.disk_percent == 55.0
        assert sample.load_avg_1m == 0.2

    def test_evaluate_available_when_under_thresholds(self, checker):
        sample = ResourceSample(
            cpu_percent=10, mem_percent=20, disk_percent=30, load_avg_1m=0.1, timestamp=time.time()
        )
        decision = checker.evaluate(sample)
        assert decision.available is True
        assert len(decision.reasons) == 0

    def test_evaluate_unavailable_when_cpu_exceeds(self, checker):
        sample = ResourceSample(
            cpu_percent=60, mem_percent=20, disk_percent=30, load_avg_1m=0.1, timestamp=time.time()
        )
        decision = checker.evaluate(sample)
        assert decision.available is False
        assert any("cpu" in r for r in decision.reasons)

    def test_evaluate_unavailable_when_mem_exceeds(self, checker):
        sample = ResourceSample(
            cpu_percent=10, mem_percent=80, disk_percent=30, load_avg_1m=0.1, timestamp=time.time()
        )
        decision = checker.evaluate(sample)
        assert decision.available is False
        assert any("mem" in r for r in decision.reasons)

    def test_evaluate_unavailable_when_disk_exceeds(self, checker):
        sample = ResourceSample(
            cpu_percent=10, mem_percent=20, disk_percent=95, load_avg_1m=0.1, timestamp=time.time()
        )
        decision = checker.evaluate(sample)
        assert decision.available is False
        assert any("disk" in r for r in decision.reasons)

    def test_evaluate_unavailable_when_load_exceeds(self, checker):
        sample = ResourceSample(
            cpu_percent=10, mem_percent=20, disk_percent=30, load_avg_1m=3.0, timestamp=time.time()
        )
        decision = checker.evaluate(sample)
        assert decision.available is False
        assert any("load" in r for r in decision.reasons)

    def test_evaluate_reports_all_exceeded_reasons(self, checker):
        sample = ResourceSample(
            cpu_percent=80, mem_percent=90, disk_percent=95, load_avg_1m=5.0, timestamp=time.time()
        )
        decision = checker.evaluate(sample)
        assert decision.available is False
        assert len(decision.reasons) == 4

    def test_check_available_combines_sample_and_evaluate(self, checker):
        with patch.object(checker, "sample") as mock_sample, patch.object(checker, "evaluate") as mock_eval:
            mock_sample.return_value = MagicMock()
            mock_eval.return_value = AvailabilityDecision(available=True, reasons=[], sample=mock_sample.return_value)
            decision = checker.check_available()
            assert decision.available is True
            mock_sample.assert_called_once()
            mock_eval.assert_called_once()
