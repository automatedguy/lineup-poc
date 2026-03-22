"""Configuration for Lineup.

Centralizes all settings with sensible defaults for the POC.
Uses environment variables for overrides without external config files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class OllamaConfig:
    base_url: str = field(
        default_factory=lambda: os.getenv("LINEUP_OLLAMA_URL", "http://localhost:11434")
    )
    model: str = field(
        default_factory=lambda: os.getenv("LINEUP_MODEL", "llama3.1:8b")
    )
    timeout: int = 120
    temperature: float = 0.1
    max_tokens: int = 4096


@dataclass
class BrowserConfig:
    headless: bool = field(
        default_factory=lambda: os.getenv("LINEUP_HEADLESS", "true").lower() == "true"
    )
    viewport_width: int = 1280
    viewport_height: int = 720
    timeout: int = 30_000  # ms
    screenshots_dir: str = field(
        default_factory=lambda: os.getenv("LINEUP_SCREENSHOTS_DIR", "./lineup-output/screenshots")
    )


@dataclass
class ExplorerConfig:
    max_depth: int = 3
    max_pages: int = 50
    wait_after_navigation: int = 2_000  # ms
    ignore_patterns: list[str] = field(
        default_factory=lambda: [
            "logout", "signout", "sign-out", "delete-account",
            "#", "javascript:void", "mailto:", "tel:",
        ]
    )


@dataclass
class GeminiConfig:
    api_key: str = field(
        default_factory=lambda: os.getenv("GOOGLE_API_KEY", "")
    )
    model: str = field(
        default_factory=lambda: os.getenv("LINEUP_GEMINI_MODEL", "gemini-2.5-flash")
    )
    max_tokens: int = 16384
    temperature: float = 0.1


@dataclass
class ClaudeConfig:
    api_key: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", "")
    )
    model: str = field(
        default_factory=lambda: os.getenv("LINEUP_CLAUDE_MODEL", "claude-sonnet-4-20250514")
    )
    max_tokens: int = 16384
    temperature: float = 0.1


@dataclass
class ScanConfig:
    provider: str = field(
        default_factory=lambda: os.getenv("LINEUP_PROVIDER", "ollama")
    )
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    gemini: GeminiConfig = field(default_factory=GeminiConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    explorer: ExplorerConfig = field(default_factory=ExplorerConfig)
    output_dir: str = field(
        default_factory=lambda: os.getenv("LINEUP_OUTPUT_DIR", "./lineup-output")
    )
    max_test_cases: int = 20
