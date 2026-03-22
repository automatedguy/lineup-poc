"""Tests for the learning store (SQLite persistence)."""

import tempfile

import pytest

from lineup.core.models import (
    AppMap,
    Bug,
    ScanReport,
    Severity,
    TestAction,
    TestCase,
    TestResult,
)
from lineup.learning.store import LearningStore


@pytest.fixture
def store():
    """Create a LearningStore in a temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        s = LearningStore(tmpdir)
        yield s
        s.close()


@pytest.fixture
def sample_passing_result():
    tc = TestCase(
        id="tc-1", name="Login Test", description="Test login",
        target_url="http://localhost:3000/login",
        actions=[
            TestAction(action="navigate", value="http://localhost:3000/login"),
            TestAction(action="type", selector="#email", value="test@test.com", description="Email"),
            TestAction(action="type", selector="#password", value="123", description="Password"),
            TestAction(action="click", selector="#loginButton", description="Login"),
        ],
        expected_behavior="User logs in", category="functional",
    )
    return TestResult(test_case=tc, passed=True, actual_behavior="All actions completed")


@pytest.fixture
def sample_failing_result():
    tc = TestCase(
        id="tc-2", name="Bad Selector Test", description="",
        target_url="http://localhost:3000/login",
        actions=[
            TestAction(action="navigate", value="http://localhost:3000/login"),
            TestAction(action="type", selector="#email", value="x", description="Email"),
            TestAction(action="click", selector=".nonexistent", description="Click bad"),
        ],
        expected_behavior="Should fail", category="functional",
    )
    return TestResult(
        test_case=tc, passed=False,
        actual_behavior="Action 'click' failed at step 3: Click bad",
        error_message="Element not found",
    )


@pytest.fixture
def sample_bug():
    return Bug(
        id="bug-1", title="SQL injection in login", description="desc",
        severity=Severity.HIGH, url="http://localhost:3000/login",
    )


@pytest.fixture
def sample_report(sample_passing_result, sample_failing_result, sample_bug):
    return ScanReport(
        target_url="http://localhost:3000",
        app_map=AppMap(base_url="http://localhost:3000"),
        test_cases_generated=2, test_cases_executed=2,
        test_cases_passed=1, test_cases_failed=1,
        bugs=[sample_bug],
        results=[sample_passing_result, sample_failing_result],
        duration_seconds=10.0, model_used="gemini-2.5-flash",
    )


# --- Helpers ---


class TestHelpers:
    def test_domain_from_url(self):
        assert LearningStore.domain_from_url("http://localhost:3000/login") == "localhost:3000"
        assert LearningStore.domain_from_url("https://example.com/path") == "example.com"

    def test_normalize_page_url(self):
        norm = LearningStore._normalize_page_url
        assert norm("http://localhost:3000/login?ref=home") == "http://localhost:3000/login"
        assert norm("http://localhost:3000/login#section") == "http://localhost:3000/login"
        assert norm("http://localhost:3000/login/") == "http://localhost:3000/login"


# --- Empty store ---


class TestEmptyStore:
    def test_scan_count_zero(self, store):
        assert store.get_scan_count("localhost:3000") == 0

    def test_empty_learning_context(self, store):
        ctx = store.build_learning_context("localhost:3000", "http://localhost:3000/login")
        assert ctx == ""

    def test_no_good_selectors(self, store):
        assert store.get_good_selectors("localhost:3000", "http://localhost:3000") == []

    def test_no_bad_selectors(self, store):
        assert store.get_bad_selectors("localhost:3000", "http://localhost:3000") == []

    def test_no_known_bugs(self, store):
        assert store.get_known_bugs("localhost:3000") == []


# --- Recording results ---


class TestRecordResults:
    def test_passing_result_increments_ok(self, store, sample_passing_result):
        store.record_results("localhost:3000", [sample_passing_result])
        good = store.get_good_selectors("localhost:3000", "http://localhost:3000/login")
        selectors = {s["selector"] for s in good}
        assert "#email" in selectors
        assert "#password" in selectors
        assert "#loginButton" in selectors

    def test_failing_result_tracks_bad_selector(self, store, sample_failing_result):
        store.record_results("localhost:3000", [sample_failing_result])
        bad = store.get_bad_selectors("localhost:3000", "http://localhost:3000/login")
        selectors = {s["selector"] for s in bad}
        assert ".nonexistent" in selectors

    def test_failing_result_ok_before_failure(self, store, sample_failing_result):
        """Selectors used before the failing step should be marked as OK."""
        store.record_results("localhost:3000", [sample_failing_result])
        good = store.get_good_selectors("localhost:3000", "http://localhost:3000/login")
        selectors = {s["selector"] for s in good}
        # #email was step 2, failure at step 3 → #email should be OK
        assert "#email" in selectors

    def test_upsert_increments(self, store, sample_passing_result):
        store.record_results("localhost:3000", [sample_passing_result])
        store.record_results("localhost:3000", [sample_passing_result])
        good = store.get_good_selectors("localhost:3000", "http://localhost:3000/login")
        email_stats = next(s for s in good if s["selector"] == "#email")
        assert email_stats["ok"] == 2

    def test_navigate_and_assert_ignored(self, store):
        """Navigate, assert, and wait actions should not be tracked."""
        tc = TestCase(
            id="tc-x", name="Nav", description="", target_url="http://localhost:3000",
            actions=[
                TestAction(action="navigate", value="http://localhost:3000"),
                TestAction(action="assert", description="Page loads"),
                TestAction(action="wait", value="1000"),
            ],
        )
        result = TestResult(test_case=tc, passed=True, actual_behavior="OK")
        store.record_results("localhost:3000", [result])
        good = store.get_good_selectors("localhost:3000", "http://localhost:3000")
        assert good == []


# --- Recording bugs ---


class TestRecordBugs:
    def test_record_single_bug(self, store, sample_bug):
        store.record_bugs("localhost:3000", [sample_bug])
        bugs = store.get_known_bugs("localhost:3000")
        assert len(bugs) == 1
        assert bugs[0]["title"] == "SQL injection in login"
        assert bugs[0]["severity"] == "high"
        assert bugs[0]["times_seen"] == 1

    def test_upsert_increments_times_seen(self, store, sample_bug):
        store.record_bugs("localhost:3000", [sample_bug])
        store.record_bugs("localhost:3000", [sample_bug])
        bugs = store.get_known_bugs("localhost:3000")
        assert bugs[0]["times_seen"] == 2

    def test_mark_fixed_bugs(self, store, sample_bug):
        store.record_bugs("localhost:3000", [sample_bug])
        store.mark_fixed_bugs("localhost:3000", set())  # No active bugs
        bugs = store.get_known_bugs("localhost:3000")
        assert len(bugs) == 0  # Bug marked as not active

    def test_mark_fixed_preserves_active(self, store, sample_bug):
        store.record_bugs("localhost:3000", [sample_bug])
        store.mark_fixed_bugs("localhost:3000", {"SQL injection in login"})
        bugs = store.get_known_bugs("localhost:3000")
        assert len(bugs) == 1  # Still active


# --- Recording scans ---


class TestRecordScan:
    def test_record_and_count(self, store, sample_report):
        assert store.get_scan_count("localhost:3000") == 0
        store.record_scan(sample_report)
        assert store.get_scan_count("localhost:3000") == 1

    def test_multiple_scans(self, store, sample_report):
        store.record_scan(sample_report)
        store.record_scan(sample_report)
        assert store.get_scan_count("localhost:3000") == 2

    def test_different_domains_independent(self, store, sample_report):
        store.record_scan(sample_report)
        assert store.get_scan_count("localhost:3000") == 1
        assert store.get_scan_count("example.com") == 0


# --- Learning context ---


class TestBuildLearningContext:
    def test_includes_all_sections(self, store, sample_passing_result, sample_failing_result, sample_bug, sample_report):
        store.record_results("localhost:3000", [sample_passing_result, sample_failing_result])
        store.record_bugs("localhost:3000", [sample_bug])
        store.record_scan(sample_report)

        ctx = store.build_learning_context("localhost:3000", "http://localhost:3000/login")

        assert "PREVIOUS SCANS" in ctx
        assert "VERIFIED WORKING" in ctx
        assert "#email" in ctx
        assert "DO NOT USE" in ctx
        assert ".nonexistent" in ctx
        assert "KNOWN BUGS" in ctx
        assert "SQL injection" in ctx

    def test_different_page_no_selectors(self, store, sample_passing_result, sample_report):
        store.record_results("localhost:3000", [sample_passing_result])
        store.record_scan(sample_report)

        # Query for a different page
        ctx = store.build_learning_context("localhost:3000", "http://localhost:3000/signup")
        # Should not include login selectors, but may include domain-level bugs
        assert "#email" not in ctx

    def test_unknown_domain_empty(self, store, sample_report):
        store.record_scan(sample_report)
        ctx = store.build_learning_context("unknown.com", "http://unknown.com")
        assert ctx == ""
