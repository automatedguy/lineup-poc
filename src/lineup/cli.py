"""Lineup CLI.

The command-line interface. Keeps it simple for the POC:
just `lineup scan <url>` and it does everything.
"""

from __future__ import annotations

import asyncio
import sys

import click
from rich.console import Console

from lineup import __version__
from lineup.core.config import ScanConfig

console = Console()


@click.group()
@click.version_option(version=__version__, prog_name="lineup")
def main() -> None:
    """Lineup — Autonomous Testing Platform."""
    pass


@main.command()
@click.argument("url")
@click.option("--model", default=None, help="Ollama model to use (default: llama3.1:8b)")
@click.option("--depth", default=3, help="Max exploration depth (default: 3)")
@click.option("--max-tests", default=20, help="Max test cases to generate (default: 20)")
@click.option("--output", default="./lineup-output", help="Output directory")
@click.option("--headed", is_flag=True, help="Run browser in visible mode")
@click.option("--ollama-url", default=None, help="Ollama API URL (default: http://localhost:11434)")
@click.option("--provider", default=None, type=click.Choice(["ollama", "claude"]), help="LLM provider (default: ollama)")
def scan(
    url: str,
    model: str | None,
    depth: int,
    max_tests: int,
    output: str,
    headed: bool,
    ollama_url: str | None,
    provider: str | None,
) -> None:
    """Scan a web application for bugs.

    Example: lineup scan https://juice-shop.herokuapp.com
    """
    config = ScanConfig()
    config.output_dir = output
    config.explorer.max_depth = depth
    config.max_test_cases = max_tests

    if provider:
        config.provider = provider
    if model:
        if config.provider == "claude":
            config.claude.model = model
        else:
            config.ollama.model = model
    if headed:
        config.browser.headless = False
    if ollama_url:
        config.ollama.base_url = ollama_url

    config.browser.screenshots_dir = f"{output}/screenshots"

    from lineup.scan import run_scan

    try:
        asyncio.run(run_scan(url, config))
    except ConnectionError:
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Scan cancelled.[/]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[bold red]Error:[/] {e}")
        sys.exit(1)


@main.command()
@click.option("--ollama-url", default="http://localhost:11434", help="Ollama API URL")
def check(ollama_url: str) -> None:
    """Check if Ollama is running and ready."""
    import httpx

    console.print(f"Checking Ollama at {ollama_url}...")
    try:
        resp = httpx.get(f"{ollama_url}/api/tags", timeout=10)
        if resp.status_code == 200:
            models = [m["name"] for m in resp.json().get("models", [])]
            console.print(f"  [green]Connected[/]")
            if models:
                console.print(f"  Available models:")
                for m in models:
                    console.print(f"    - {m}")
            else:
                console.print(f"  [yellow]No models installed.[/]")
                console.print(f"  Run: ollama pull llama3.1:8b")
        else:
            console.print(f"  [red]Unexpected status: {resp.status_code}[/]")
    except Exception as e:
        console.print(f"  [red]Cannot connect: {e}[/]")
        console.print(f"  Make sure Ollama is running: ollama serve")


if __name__ == "__main__":
    main()
