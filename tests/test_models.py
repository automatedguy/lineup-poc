"""Tests for core Pydantic models."""

import pytest
from pydantic import ValidationError

from lineup.core.models import (
    AppMap,
    Bug,
    ElementType,
    PageElement,
    PageSnapshot,
    Route,
    ScanReport,
    Severity,
    TestAction,
    TestCase,
    TestResult,
)


# --- TestAction ---


class TestTestAction:
    def test_basic_creation(self):
        action = TestAction(action="click", selector="#btn", description="Click button")
        assert action.action == "click"
        assert action.selector == "#btn"
        assert action.value is None

    def test_value_coercion_from_int(self):
        action = TestAction(action="wait", value=1000)
        assert action.value == "1000"
        assert isinstance(action.value, str)

    def test_value_coercion_from_bool(self):
        action = TestAction(action="click", value=True)
        assert action.value == "True"
        assert isinstance(action.value, str)

    def test_value_coercion_from_float(self):
        action = TestAction(action="wait", value=1.5)
        assert action.value == "1.5"

    def test_value_none_stays_none(self):
        action = TestAction(action="click")
        assert action.value is None

    def test_value_string_unchanged(self):
        action = TestAction(action="type", value="hello@test.com")
        assert action.value == "hello@test.com"

    def test_defaults(self):
        action = TestAction(action="click")
        assert action.selector is None
        assert action.value is None
        assert action.description == ""


# --- TestCase ---


class TestTestCase:
    def test_creation_with_actions(self):
        actions = [
            TestAction(action="navigate", value="http://localhost"),
            TestAction(action="click", selector="#btn"),
        ]
        tc = TestCase(
            id="tc-1", name="Test", description="A test",
            target_url="http://localhost", actions=actions,
        )
        assert len(tc.actions) == 2
        assert tc.actions[0].action == "navigate"

    def test_empty_actions_default(self):
        tc = TestCase(id="tc-1", name="Test", description="", target_url="http://localhost")
        assert tc.actions == []

    def test_category_default(self):
        tc = TestCase(id="tc-1", name="Test", description="", target_url="http://localhost")
        assert tc.category == ""


# --- TestResult ---


class TestTestResult:
    def test_passing_result(self):
        tc = TestCase(id="tc-1", name="Test", description="", target_url="http://localhost")
        result = TestResult(test_case=tc, passed=True, actual_behavior="All good")
        assert result.passed is True
        assert result.error_message is None

    def test_failing_result(self):
        tc = TestCase(id="tc-1", name="Test", description="", target_url="http://localhost")
        result = TestResult(
            test_case=tc, passed=False,
            actual_behavior="Action 'click' failed at step 2",
            error_message="Element not found",
        )
        assert result.passed is False
        assert "Element not found" in result.error_message


# --- Severity & ElementType ---


class TestEnums:
    def test_severity_values(self):
        assert Severity.CRITICAL == "critical"
        assert Severity.INFO == "info"

    def test_element_type_values(self):
        assert ElementType.BUTTON == "button"
        assert ElementType.INPUT == "input"

    def test_severity_from_string(self):
        assert Severity("high") == Severity.HIGH


# --- PageElement ---


class TestPageElement:
    def test_creation(self):
        el = PageElement(
            selector="#email", element_type=ElementType.INPUT,
            text="", attributes={"type": "email", "placeholder": "Email"},
        )
        assert el.selector == "#email"
        assert el.is_visible is True

    def test_defaults(self):
        el = PageElement(selector="a", element_type=ElementType.LINK)
        assert el.text == ""
        assert el.attributes == {}
        assert el.is_visible is True


# --- Bug ---


class TestBug:
    def test_creation(self):
        bug = Bug(
            id="bug-1", title="XSS in search",
            description="Reflected XSS", severity=Severity.HIGH,
            url="http://localhost/search",
            steps_to_reproduce=["Go to search", "Enter <script>"],
        )
        assert bug.severity == Severity.HIGH
        assert len(bug.steps_to_reproduce) == 2

    def test_defaults(self):
        bug = Bug(
            id="bug-1", title="Bug", description="",
            severity=Severity.LOW, url="http://localhost",
        )
        assert bug.steps_to_reproduce == []
        assert bug.screenshots == []
        assert bug.test_result is None


# --- ScanReport ---


class TestScanReport:
    def test_creation(self):
        report = ScanReport(
            target_url="http://localhost",
            app_map=AppMap(base_url="http://localhost"),
            test_cases_generated=10, test_cases_passed=7, test_cases_failed=3,
        )
        assert report.test_cases_generated == 10
        assert report.bugs == []
        assert report.model_used == ""
