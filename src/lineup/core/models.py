"""Domain models for Lineup.

These models define the data structures that flow between components.
They are the shared language of the system — explorer, generator,
executor, and reporter all speak through these types.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ElementType(str, Enum):
    LINK = "link"
    BUTTON = "button"
    INPUT = "input"
    SELECT = "select"
    TEXTAREA = "textarea"
    FORM = "form"
    IMAGE = "image"
    OTHER = "other"


class PageElement(BaseModel):
    """An interactive element discovered on a page."""

    selector: str
    element_type: ElementType
    text: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)
    is_visible: bool = True


class PageSnapshot(BaseModel):
    """A snapshot of a page at a point in time."""

    url: str
    title: str
    html_summary: str = ""
    elements: list[PageElement] = Field(default_factory=list)
    screenshot_path: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)


class Route(BaseModel):
    """A discovered route/page in the application."""

    url: str
    title: str = ""
    depth: int = 0
    discovered_from: str | None = None
    elements: list[PageElement] = Field(default_factory=list)


class AppMap(BaseModel):
    """The discovered structure of the application under test."""

    base_url: str
    routes: list[Route] = Field(default_factory=list)
    total_elements: int = 0
    scan_duration_seconds: float = 0.0


class TestAction(BaseModel):
    """A single action in a test case."""

    action: str  # click, type, select, navigate, assert, wait
    selector: str | None = None
    value: str | None = None
    description: str = ""


class TestCase(BaseModel):
    """A generated test case to execute."""

    id: str
    name: str
    description: str
    target_url: str
    actions: list[TestAction] = Field(default_factory=list)
    expected_behavior: str = ""
    category: str = ""  # functional, edge_case, validation, security


class TestResult(BaseModel):
    """The result of executing a test case."""

    test_case: TestCase
    passed: bool
    actual_behavior: str = ""
    error_message: str | None = None
    screenshots: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.now)


class Bug(BaseModel):
    """A discovered bug with evidence."""

    id: str
    title: str
    description: str
    severity: Severity
    url: str
    steps_to_reproduce: list[str] = Field(default_factory=list)
    expected: str = ""
    actual: str = ""
    screenshots: list[str] = Field(default_factory=list)
    test_result: TestResult | None = None
    timestamp: datetime = Field(default_factory=datetime.now)


class ScanReport(BaseModel):
    """The final report of a Lineup scan."""

    target_url: str
    app_map: AppMap
    test_cases_generated: int = 0
    test_cases_executed: int = 0
    test_cases_passed: int = 0
    test_cases_failed: int = 0
    bugs: list[Bug] = Field(default_factory=list)
    results: list[TestResult] = Field(default_factory=list)
    duration_seconds: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.now)
    model_used: str = ""
