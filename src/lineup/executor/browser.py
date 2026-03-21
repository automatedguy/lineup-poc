"""Test executor using Playwright.

Runs generated test cases against the application by translating
TestAction sequences into browser automation commands.
"""

from __future__ import annotations

import os
import time

from playwright.async_api import Browser, Page, async_playwright
from rich.console import Console

from lineup.core.config import ScanConfig
from lineup.core.interfaces import TestExecutor
from lineup.core.models import TestCase, TestResult

console = Console()


class BrowserExecutor(TestExecutor):
    """Executes test cases using Playwright browser automation."""

    def __init__(self, config: ScanConfig) -> None:
        self.config = config
        self._browser: Browser | None = None

    async def _get_browser(self) -> Browser:
        if self._browser is None:
            pw = await async_playwright().start()
            self._browser = await pw.chromium.launch(
                headless=self.config.browser.headless
            )
        return self._browser

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None

    async def _take_screenshot(self, page: Page, test_id: str, suffix: str) -> str:
        """Take a screenshot and return the path."""
        screenshots_dir = self.config.browser.screenshots_dir
        os.makedirs(screenshots_dir, exist_ok=True)
        path = os.path.join(screenshots_dir, f"{test_id}_{suffix}.png")
        await page.screenshot(path=path)
        return path

    async def execute(self, test_case: TestCase) -> TestResult:
        """Execute a single test case."""
        start_time = time.time()
        screenshots: list[str] = []
        browser = await self._get_browser()

        page = await browser.new_page(
            viewport={
                "width": self.config.browser.viewport_width,
                "height": self.config.browser.viewport_height,
            }
        )

        try:
            for i, action in enumerate(test_case.actions):
                try:
                    if action.action == "navigate":
                        url = action.value or test_case.target_url
                        await page.goto(url, wait_until="domcontentloaded",
                                      timeout=self.config.browser.timeout)
                        await page.wait_for_timeout(1000)

                    elif action.action == "click":
                        if not action.selector:
                            continue
                        loc = page.locator(action.selector).first
                        await loc.wait_for(state="visible", timeout=5000)
                        await loc.click(timeout=5000)
                        await page.wait_for_timeout(500)

                    elif action.action == "type":
                        if not action.selector:
                            continue
                        loc = page.locator(action.selector).first
                        await loc.wait_for(state="visible", timeout=5000)
                        await loc.fill(action.value or "")
                        await page.wait_for_timeout(300)

                    elif action.action == "select":
                        if not action.selector:
                            continue
                        loc = page.locator(action.selector).first
                        await loc.select_option(value=action.value)

                    elif action.action == "wait":
                        ms = int(action.value or "1000")
                        await page.wait_for_timeout(min(ms, 5000))

                    elif action.action == "assert":
                        # For POC: take a screenshot at assertion points
                        # The bug analyzer will evaluate visually
                        path = await self._take_screenshot(
                            page, test_case.id, f"assert_{i}"
                        )
                        screenshots.append(path)

                except Exception as e:
                    # Action failed — this might be a bug
                    path = await self._take_screenshot(
                        page, test_case.id, f"error_{i}"
                    )
                    screenshots.append(path)

                    duration = time.time() - start_time
                    return TestResult(
                        test_case=test_case,
                        passed=False,
                        actual_behavior=f"Action '{action.action}' failed at step {i + 1}: {action.description}",
                        error_message=str(e),
                        screenshots=screenshots,
                        duration_seconds=duration,
                    )

            # All actions completed — take final screenshot
            path = await self._take_screenshot(page, test_case.id, "final")
            screenshots.append(path)

            # Check for JavaScript errors on the page
            js_errors = await page.evaluate("""() => {
                const errors = window.__lineup_errors || [];
                return errors;
            }""")

            # Check for visible error indicators
            error_visible = await page.evaluate("""() => {
                const selectors = [
                    '.error', '.alert-danger', '.alert-error',
                    '[role="alert"]', '.error-message', '.form-error',
                    '.invalid-feedback', '.field-error'
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.offsetParent !== null && el.textContent.trim()) {
                        return el.textContent.trim().substring(0, 200);
                    }
                }
                return null;
            }""")

            duration = time.time() - start_time

            if error_visible:
                return TestResult(
                    test_case=test_case,
                    passed=False,
                    actual_behavior=f"Error visible on page: {error_visible}",
                    screenshots=screenshots,
                    duration_seconds=duration,
                )

            return TestResult(
                test_case=test_case,
                passed=True,
                actual_behavior="All actions completed successfully",
                screenshots=screenshots,
                duration_seconds=duration,
            )

        except Exception as e:
            duration = time.time() - start_time
            try:
                path = await self._take_screenshot(page, test_case.id, "crash")
                screenshots.append(path)
            except Exception:
                pass

            return TestResult(
                test_case=test_case,
                passed=False,
                actual_behavior=f"Test execution crashed: {e}",
                error_message=str(e),
                screenshots=screenshots,
                duration_seconds=duration,
            )

        finally:
            await page.close()

    async def execute_batch(self, test_cases: list[TestCase]) -> list[TestResult]:
        """Execute multiple test cases sequentially.

        In the POC this is sequential. The interface is ready for
        parallel execution when distributed.
        """
        results: list[TestResult] = []
        total = len(test_cases)

        for i, tc in enumerate(test_cases, 1):
            console.print(f"  [{i}/{total}] {tc.name} ", end="")
            result = await self.execute(tc)

            if result.passed:
                console.print("[green]PASS[/]")
            else:
                console.print(f"[red]FAIL[/] — {result.actual_behavior[:60]}")

            results.append(result)

        return results
