"""Abstract interfaces for Lineup components.

These interfaces define the contracts between components. The POC
implements each with a single concrete class, but the interfaces
are ready for multiple implementations when scaling:

- Explorer: could become distributed (multiple crawlers in parallel)
- Generator: could swap LLM backends (Ollama, OpenAI, Anthropic)
- Executor: could run across multiple browsers/machines
- Reporter: could output to different formats (HTML, PDF, Jira, Slack)
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from lineup.core.models import (
    AppMap,
    Bug,
    PageSnapshot,
    ScanReport,
    TestCase,
    TestResult,
)


class Explorer(ABC):
    """Discovers the structure of an application."""

    @abstractmethod
    async def explore(self, base_url: str, max_depth: int = 3) -> AppMap:
        ...

    @abstractmethod
    async def take_snapshot(self, url: str) -> PageSnapshot:
        ...


class TestGenerator(ABC):
    """Generates test cases from application structure."""

    @abstractmethod
    async def generate(
        self, app_map: AppMap, snapshots: list[PageSnapshot]
    ) -> list[TestCase]:
        ...


class TestExecutor(ABC):
    """Executes test cases against the application."""

    @abstractmethod
    async def execute(self, test_case: TestCase) -> TestResult:
        ...

    @abstractmethod
    async def execute_batch(self, test_cases: list[TestCase]) -> list[TestResult]:
        ...


class BugAnalyzer(ABC):
    """Analyzes test results to identify and classify bugs."""

    @abstractmethod
    async def analyze(self, results: list[TestResult]) -> list[Bug]:
        ...


class Reporter(ABC):
    """Generates reports from scan results."""

    @abstractmethod
    async def generate_report(self, report: ScanReport, output_dir: str) -> str:
        ...
