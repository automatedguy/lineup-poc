"""Test generator using Claude API.

Same logic as the Ollama generator but uses the Anthropic API.
"""

from __future__ import annotations

import json
import uuid

import anthropic
from rich.console import Console

from lineup.core.config import ScanConfig
from lineup.core.interfaces import BugAnalyzer, TestGenerator
from lineup.core.models import (
    AppMap,
    Bug,
    PageSnapshot,
    Severity,
    TestAction,
    TestCase,
    TestResult,
)
from lineup.generator.llm import SYSTEM_PROMPT_ANALYZER, SYSTEM_PROMPT_GENERATOR

console = Console()


class ClaudeClient:
    """Thin wrapper around the Anthropic API."""

    def __init__(self, config: ScanConfig) -> None:
        self.model = config.claude.model
        self.max_tokens = config.claude.max_tokens
        self.temperature = config.claude.temperature
        self.client = anthropic.AsyncAnthropic(api_key=config.claude.api_key)

    async def check_health(self) -> bool:
        """Verify the API key works by making a minimal request."""
        try:
            await self.client.messages.create(
                model=self.model,
                max_tokens=10,
                messages=[{"role": "user", "content": "ping"}],
            )
            return True
        except Exception:
            return False

    async def generate_json(self, prompt: str, system: str = "") -> dict | list:
        """Send a prompt to Claude and parse the JSON response."""
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0]
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0]
            return json.loads(raw.strip())


class ClaudeTestGenerator(TestGenerator):
    """Generates test cases using Claude API."""

    def __init__(self, config: ScanConfig) -> None:
        self.config = config
        self.client = ClaudeClient(config)

    def _build_page_context(self, snapshot: PageSnapshot) -> str:
        """Build a concise page description for the LLM."""
        elements_desc = []
        for el in snapshot.elements:
            if not el.is_visible:
                continue
            desc = f"- {el.element_type.value}: selector='{el.selector}'"
            if el.text:
                desc += f" text='{el.text[:50]}'"
            if el.attributes:
                attrs = ", ".join(f"{k}={v[:30]}" for k, v in el.attributes.items())
                desc += f" [{attrs}]"
            elements_desc.append(desc)

        return f"""Page: {snapshot.title}
URL: {snapshot.url}

Interactive elements:
{chr(10).join(elements_desc[:80])}

HTML structure (truncated):
{snapshot.html_summary[:3000]}"""

    async def generate(
        self, app_map: AppMap, snapshots: list[PageSnapshot]
    ) -> list[TestCase]:
        """Generate test cases for discovered pages."""
        all_tests: list[TestCase] = []

        for snapshot in snapshots:
            if not snapshot.elements:
                continue

            console.print(f"  [dim]Generating tests for[/] {snapshot.url}")

            context = self._build_page_context(snapshot)
            prompt = f"""{context}

Generate 3-5 test cases for this page. Return JSON with this structure:
{{
  "test_cases": [
    {{
      "name": "short descriptive name",
      "description": "what this test verifies",
      "category": "functional|validation|edge_case|security",
      "expected_behavior": "what should happen",
      "actions": [
        {{"action": "navigate", "value": "{snapshot.url}"}},
        {{"action": "type", "selector": "#email", "value": "test@example.com", "description": "Enter email"}},
        {{"action": "click", "selector": "button[type='submit']", "description": "Submit form"}},
        {{"action": "assert", "description": "Form submits without errors"}}
      ]
    }}
  ]
}}"""

            try:
                result = await self.client.generate_json(prompt, SYSTEM_PROMPT_GENERATOR)

                if isinstance(result, dict):
                    cases = result.get("test_cases", [])
                elif isinstance(result, list):
                    cases = result
                else:
                    console.print(f"    [red]Generation failed: unexpected response type ({type(result).__name__})[/]")
                    continue

                for tc_data in cases:
                    if not isinstance(tc_data, dict):
                        continue
                    actions = [
                        TestAction(
                            action=a.get("action", ""),
                            selector=a.get("selector"),
                            value=a.get("value"),
                            description=a.get("description", ""),
                        )
                        for a in tc_data.get("actions", [])
                        if isinstance(a, dict)
                    ]

                    test_case = TestCase(
                        id=f"tc-{uuid.uuid4().hex[:8]}",
                        name=tc_data.get("name", "Unnamed test"),
                        description=tc_data.get("description", ""),
                        target_url=snapshot.url,
                        actions=actions,
                        expected_behavior=tc_data.get("expected_behavior", ""),
                        category=tc_data.get("category", "functional"),
                    )
                    all_tests.append(test_case)

                console.print(f"    [green]{len(cases)} tests generated[/]")

            except Exception as e:
                console.print(f"    [red]Generation failed: {e}[/]")

            if len(all_tests) >= self.config.max_test_cases:
                break

        return all_tests[:self.config.max_test_cases]


class ClaudeBugAnalyzer(BugAnalyzer):
    """Analyzes test results using Claude to identify real bugs."""

    def __init__(self, config: ScanConfig) -> None:
        self.config = config
        self.client = ClaudeClient(config)

    async def analyze(self, results: list[TestResult]) -> list[Bug]:
        """Analyze failed test results and extract bugs."""
        failed = [r for r in results if not r.passed]
        if not failed:
            return []

        console.print(f"\n[bold cyan]Analyzing[/] {len(failed)} failed tests")

        failures_desc = []
        for r in failed:
            desc = f"""Test: {r.test_case.name}
URL: {r.test_case.target_url}
Category: {r.test_case.category}
Expected: {r.test_case.expected_behavior}
Actual: {r.actual_behavior}
Error: {r.error_message or 'None'}"""
            failures_desc.append(desc)

        prompt = f"""Analyze these test failures and identify real bugs.
Deduplicate — group failures with the same root cause.

Failures:
{chr(10).join(failures_desc)}

Return JSON:
{{
  "bugs": [
    {{
      "title": "short bug title",
      "description": "detailed description",
      "severity": "critical|high|medium|low|info",
      "url": "affected URL",
      "steps_to_reproduce": ["step 1", "step 2"],
      "expected": "expected behavior",
      "actual": "actual behavior"
    }}
  ]
}}"""

        try:
            result = await self.client.generate_json(prompt, SYSTEM_PROMPT_ANALYZER)
            if isinstance(result, dict):
                bugs_data = result.get("bugs", [])
            elif isinstance(result, list):
                bugs_data = result
            else:
                console.print(f"  [red]Analysis failed: unexpected response type ({type(result).__name__})[/]")
                return []

            bugs = []
            for bd in bugs_data:
                if not isinstance(bd, dict):
                    continue
                bug = Bug(
                    id=f"bug-{uuid.uuid4().hex[:8]}",
                    title=bd.get("title", "Unknown bug"),
                    description=bd.get("description", ""),
                    severity=Severity(bd.get("severity", "medium")),
                    url=bd.get("url", ""),
                    steps_to_reproduce=bd.get("steps_to_reproduce", []),
                    expected=bd.get("expected", ""),
                    actual=bd.get("actual", ""),
                )
                bugs.append(bug)

            console.print(f"  [green]{len(bugs)} bugs identified[/]")
            return bugs

        except Exception as e:
            console.print(f"  [red]Analysis failed: {e}[/]")
            return [
                Bug(
                    id=f"bug-{uuid.uuid4().hex[:8]}",
                    title=f"Failed: {r.test_case.name}",
                    description=r.actual_behavior,
                    severity=Severity.MEDIUM,
                    url=r.test_case.target_url,
                    expected=r.test_case.expected_behavior,
                    actual=r.actual_behavior,
                    error_message=r.error_message,
                )
                for r in failed
            ]
