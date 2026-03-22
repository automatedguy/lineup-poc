"""Test generator using Ollama LLM.

Sends page structure to a local LLM and asks it to generate
meaningful test cases. The prompt engineering here is critical —
it defines the quality of generated tests.
"""

from __future__ import annotations

import json
import re
import uuid

import httpx
from rich.console import Console

from lineup.core.config import ScanConfig
from lineup.core.interfaces import TestGenerator
from lineup.learning.store import LearningStore
from lineup.core.models import (
    AppMap,
    Bug,
    PageSnapshot,
    Severity,
    TestAction,
    TestCase,
    TestResult,
)
from lineup.core.interfaces import BugAnalyzer

console = Console()


def _strip_to_json(raw: str) -> str:
    """Extract the JSON portion from an LLM response, stripping prose/markdown."""
    # Markdown code blocks
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0]
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0]

    # Locate the first { or [
    match = re.search(r"[\[{]", raw)
    if match:
        return raw[match.start():]
    return raw.strip()


def _repair_truncated_json(text: str) -> dict | list:
    """Attempt to salvage a truncated JSON string by finding the last complete
    array element and closing any open containers.

    The key idea: walk backwards to find the last comma or closing brace that
    marks the end of a complete element, discard everything after it, then
    close the remaining open containers.
    """
    # Find the last successfully closed object/array element.
    # We look for '},' or '}]' patterns that indicate a complete element boundary.
    # Then we try closing from there.
    best: dict | list | None = None

    # Strategy: find every position of '}' or ']' and try closing from there
    for i in range(len(text) - 1, 0, -1):
        if text[i] not in ('}', ']', '"'):
            continue

        fragment = text[: i + 1]
        # Strip a trailing comma if present
        stripped = fragment.rstrip().rstrip(",")

        opens = stripped.count("{") - stripped.count("}")
        open_arr = stripped.count("[") - stripped.count("]")

        if opens < 0 or open_arr < 0:
            continue

        suffix = "]" * open_arr + "}" * opens
        try:
            best = json.loads(stripped + suffix)
            break
        except json.JSONDecodeError:
            continue

    if best is not None:
        return best

    raise json.JSONDecodeError("Could not repair truncated JSON", text, 0)


def parse_llm_json(raw: str) -> dict | list:
    """Best-effort JSON extraction from an LLM response.

    Handles: markdown code blocks, surrounding prose, and truncated JSON
    (e.g. from hitting max_tokens) by preserving as many complete elements
    as possible.
    """
    # 1. Direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 2. Strip markdown/prose, then try again
    cleaned = _strip_to_json(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 3. Truncated — repair by finding the last complete element
    return _repair_truncated_json(cleaned)


class OllamaClient:
    """Thin wrapper around the Ollama HTTP API."""

    def __init__(self, config: ScanConfig) -> None:
        self.base_url = config.ollama.base_url
        self.model = config.ollama.model
        self.timeout = config.ollama.timeout
        self.temperature = config.ollama.temperature
        self.max_tokens = config.ollama.max_tokens

    async def check_health(self) -> bool:
        """Verify Ollama is running and the model is available."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                if resp.status_code != 200:
                    return False
                data = resp.json()
                models = [m["name"] for m in data.get("models", [])]
                # Check if our model is available (with or without tag)
                model_base = self.model.split(":")[0]
                return any(model_base in m for m in models)
        except Exception:
            return False

    async def generate(self, prompt: str, system: str = "") -> str:
        """Send a prompt to Ollama and return the response."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        if system:
            payload["system"] = system

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/api/generate",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()["response"]

    async def generate_json(self, prompt: str, system: str = "") -> dict | list:
        """Generate and parse a JSON response from Ollama."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        if system:
            payload["system"] = system

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/api/generate",
                json=payload,
            )
            resp.raise_for_status()
            raw = resp.json()["response"]

        return parse_llm_json(raw)


SYSTEM_PROMPT_GENERATOR = """You are an expert QA engineer. Your job is to generate test cases
for a web application based on the page structure provided.

Generate test cases that cover:
1. FUNCTIONAL: Happy path flows (forms submit correctly, navigation works)
2. VALIDATION: Input validation (empty fields, invalid emails, XSS payloads, SQL injection)
3. EDGE CASES: Boundary values, special characters, very long inputs
4. UI: Elements are visible, clickable, properly labeled

For each test case, provide specific actions using these types:
- navigate: Go to a URL (value = URL string)
- click: Click an element (selector = CSS selector)
- type: Type text into an input (selector = CSS selector, value = text string to type)
- select: Select an option (selector = CSS selector, value = option value string)
- assert: Check something (description = what to verify)
- wait: Wait for something (value = milliseconds as a string, e.g. "1000")

CRITICAL RULES:
- Use ONLY selectors that appear in the provided page structure. Do NOT invent selectors.
- All "value" fields MUST be strings (use "1000" not 1000, "true" not true).
- Each test MUST start with a "navigate" action to the page URL.
- Return ONLY valid JSON, no explanations or markdown."""


class OllamaTestGenerator(TestGenerator):
    """Generates test cases using a local LLM via Ollama."""

    def __init__(self, config: ScanConfig) -> None:
        self.config = config
        self.client = OllamaClient(config)

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
        self, app_map: AppMap, snapshots: list[PageSnapshot],
        learning_store: LearningStore | None = None,
    ) -> list[TestCase]:
        """Generate test cases for discovered pages."""
        all_tests: list[TestCase] = []
        domain = LearningStore.domain_from_url(app_map.base_url) if learning_store else ""

        for snapshot in snapshots:
            if not snapshot.elements:
                continue

            console.print(f"  [dim]Generating tests for[/] {snapshot.url}")

            context = self._build_page_context(snapshot)

            learning_hint = ""
            if learning_store:
                learning_hint = learning_store.build_learning_context(domain, snapshot.url)

            existing_names = [t.name for t in all_tests]
            dedup_hint = ""
            if existing_names:
                dedup_hint = (
                    "\n\nDo NOT generate tests similar to these already-generated ones:\n"
                    + "\n".join(f"- {n}" for n in existing_names)
                )

            prompt = f"""{context}

Generate 3-5 test cases for this page. Focus on functionality SPECIFIC to this page.{learning_hint}{dedup_hint}

Return JSON with this structure:
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

                seen_names = {t.name.lower().strip() for t in all_tests}
                added = 0
                for tc_data in cases:
                    if not isinstance(tc_data, dict):
                        continue

                    name = tc_data.get("name", "Unnamed test")
                    if name.lower().strip() in seen_names:
                        continue
                    seen_names.add(name.lower().strip())

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
                        name=name,
                        description=tc_data.get("description", ""),
                        target_url=snapshot.url,
                        actions=actions,
                        expected_behavior=tc_data.get("expected_behavior", ""),
                        category=tc_data.get("category", "functional"),
                    )
                    all_tests.append(test_case)
                    added += 1

                console.print(f"    [green]{added} tests generated[/]")

            except Exception as e:
                console.print(f"    [red]Generation failed: {e}[/]")

            if len(all_tests) >= self.config.max_test_cases:
                break

        return all_tests[:self.config.max_test_cases]


SYSTEM_PROMPT_ANALYZER = """You are an expert QA engineer analyzing test results.
Determine if failed tests represent real bugs. For each bug:
1. Assess severity (critical, high, medium, low, info)
2. Write clear reproduction steps
3. Describe expected vs actual behavior
4. Deduplicate — if two failures are the same root cause, report once

Return ONLY valid JSON."""


class OllamaBugAnalyzer(BugAnalyzer):
    """Analyzes test results using LLM to identify real bugs."""

    def __init__(self, config: ScanConfig) -> None:
        self.config = config
        self.client = OllamaClient(config)

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
            # Fallback: create a bug entry for each failed test
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
