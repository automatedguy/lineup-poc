"""Scan orchestrator.

This is the brain of Lineup. It coordinates the full scan cycle:
explore → generate → execute → analyze → report.

In the POC everything runs in a single process. The orchestrator
is designed so that each step could be dispatched to a different
worker when the system scales to distributed execution.
"""

from __future__ import annotations

import time

from rich.console import Console
from rich.panel import Panel

from lineup.core.config import ScanConfig
from lineup.core.models import ScanReport
from lineup.explorer.web import WebExplorer
from lineup.executor.browser import BrowserExecutor
from lineup.generator.claude import ClaudeBugAnalyzer, ClaudeClient, ClaudeTestGenerator
from lineup.generator.gemini import GeminiBugAnalyzer, GeminiClient, GeminiTestGenerator
from lineup.generator.llm import OllamaBugAnalyzer, OllamaClient, OllamaTestGenerator
from lineup.reporter.html import HtmlReporter

console = Console()


async def run_scan(target_url: str, config: ScanConfig | None = None) -> ScanReport:
    """Run a full Lineup scan against a target URL.

    This is the main entry point for the scan pipeline.
    """
    if config is None:
        config = ScanConfig()

    start_time = time.time()

    if config.provider == "claude":
        model_name = config.claude.model
    elif config.provider == "gemini":
        model_name = config.gemini.model
    else:
        model_name = config.ollama.model
    console.print(Panel(
        f"[bold]line[/][bold bright_cyan]up[/] [dim]v0.1.0[/]\n"
        f"Target: {target_url}\n"
        f"Provider: {config.provider}\n"
        f"Model: {model_name}",
        border_style="cyan",
    ))

    # --- Step 0: Check LLM connection ---
    if config.provider == "claude":
        console.print("\n[bold]Step 0:[/] Checking Claude API connection...")
        llm_client = ClaudeClient(config)
        if not await llm_client.check_health():
            console.print("[bold red]Error:[/] Cannot connect to Claude API.")
            console.print("  Make sure ANTHROPIC_API_KEY is set.")
            raise ConnectionError("Claude API not available")
        console.print(f"  [green]Connected[/] — model: {config.claude.model}\n")
    elif config.provider == "gemini":
        console.print("\n[bold]Step 0:[/] Checking Gemini API connection...")
        llm_client = GeminiClient(config)
        if not await llm_client.check_health():
            console.print("[bold red]Error:[/] Cannot connect to Gemini API.")
            console.print("  Make sure GOOGLE_API_KEY is set.")
            raise ConnectionError("Gemini API not available")
        console.print(f"  [green]Connected[/] — model: {config.gemini.model}\n")
    else:
        console.print("\n[bold]Step 0:[/] Checking Ollama connection...")
        llm_client = OllamaClient(config)
        if not await llm_client.check_health():
            console.print(f"[bold red]Error:[/] Cannot connect to Ollama at {config.ollama.base_url}")
            console.print(f"  Make sure Ollama is running and model '{config.ollama.model}' is pulled.")
            console.print(f"  Run: ollama pull {config.ollama.model}")
            raise ConnectionError(f"Ollama not available at {config.ollama.base_url}")
        console.print(f"  [green]Connected[/] — model: {config.ollama.model}\n")

    # --- Step 1: Explore ---
    console.print("[bold]Step 1:[/] Exploring application...")
    explorer = WebExplorer(config)
    try:
        app_map = await explorer.explore(target_url)

        # Take snapshots of discovered pages for LLM context
        console.print("[bold]Step 1b:[/] Taking page snapshots...")
        snapshots = []
        for route in app_map.routes[:10]:  # Limit snapshots for POC
            try:
                snapshot = await explorer.take_snapshot(route.url)
                snapshots.append(snapshot)
            except Exception as e:
                console.print(f"  [yellow]Snapshot failed for {route.url}: {e}[/]")
    finally:
        await explorer.close()

    if not snapshots:
        console.print("[bold red]No pages could be explored. Aborting.[/]")
        raise RuntimeError("Exploration found no usable pages")

    # --- Step 2: Generate test cases ---
    console.print(f"\n[bold]Step 2:[/] Generating test cases for {len(snapshots)} pages...")
    if config.provider == "claude":
        generator = ClaudeTestGenerator(config)
    elif config.provider == "gemini":
        generator = GeminiTestGenerator(config)
    else:
        generator = OllamaTestGenerator(config)
    test_cases = await generator.generate(app_map, snapshots)
    console.print(f"  [green]{len(test_cases)} test cases generated[/]\n")

    if not test_cases:
        console.print("[bold yellow]No test cases generated. Check model output.[/]")
        return ScanReport(
            target_url=target_url,
            app_map=app_map,
            duration_seconds=time.time() - start_time,
            model_used=config.ollama.model,
        )

    # --- Step 3: Execute tests ---
    console.print(f"[bold]Step 3:[/] Executing {len(test_cases)} tests...\n")
    executor = BrowserExecutor(config)
    try:
        results = await executor.execute_batch(test_cases)
    finally:
        await executor.close()

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    console.print(f"\n  Results: [green]{passed} passed[/], [red]{failed} failed[/]\n")

    # --- Step 4: Analyze bugs ---
    bugs = []
    if failed > 0:
        console.print("[bold]Step 4:[/] Analyzing failures...")
        if config.provider == "claude":
            analyzer = ClaudeBugAnalyzer(config)
        elif config.provider == "gemini":
            analyzer = GeminiBugAnalyzer(config)
        else:
            analyzer = OllamaBugAnalyzer(config)
        bugs = await analyzer.analyze(results)

    # --- Step 5: Generate report ---
    console.print(f"\n[bold]Step 5:[/] Generating report...")
    report = ScanReport(
        target_url=target_url,
        app_map=app_map,
        test_cases_generated=len(test_cases),
        test_cases_executed=len(results),
        test_cases_passed=passed,
        test_cases_failed=failed,
        bugs=bugs,
        results=results,
        duration_seconds=time.time() - start_time,
        model_used=config.ollama.model,
    )

    reporter = HtmlReporter()
    report_path = await reporter.generate_report(report, config.output_dir)
    console.print(f"  [green]Report saved:[/] {report_path}\n")

    # --- Summary ---
    console.print(Panel(
        f"[bold]Scan Complete[/]\n\n"
        f"Pages scanned: {len(app_map.routes)}\n"
        f"Tests generated: {len(test_cases)}\n"
        f"Tests passed: [green]{passed}[/]\n"
        f"Tests failed: [red]{failed}[/]\n"
        f"Bugs found: [bold red]{len(bugs)}[/]\n"
        f"Duration: {report.duration_seconds:.1f}s\n"
        f"Report: {report_path}",
        title="[bold]line[/][bold bright_cyan]up[/]",
        border_style="cyan",
    ))

    return report
