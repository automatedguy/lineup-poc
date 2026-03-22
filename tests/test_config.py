"""Tests for configuration module."""

import os
from unittest.mock import patch

from lineup.core.config import (
    BrowserConfig,
    ClaudeConfig,
    ExplorerConfig,
    GeminiConfig,
    OllamaConfig,
    ScanConfig,
)


class TestOllamaConfig:
    def test_defaults(self):
        config = OllamaConfig()
        assert config.base_url == "http://localhost:11434"
        assert config.model == "llama3.1:8b"
        assert config.temperature == 0.1

    def test_env_override(self):
        with patch.dict(os.environ, {"LINEUP_MODEL": "mistral:7b"}):
            config = OllamaConfig()
            assert config.model == "mistral:7b"


class TestClaudeConfig:
    def test_defaults(self):
        config = ClaudeConfig()
        assert config.model == "claude-sonnet-4-20250514"
        assert config.max_tokens == 16384

    def test_env_override(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-123"}):
            config = ClaudeConfig()
            assert config.api_key == "sk-test-123"


class TestGeminiConfig:
    def test_defaults(self):
        config = GeminiConfig()
        assert config.model == "gemini-2.5-flash"
        assert config.max_tokens == 16384

    def test_env_override(self):
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
            config = GeminiConfig()
            assert config.api_key == "test-key"


class TestBrowserConfig:
    def test_defaults(self):
        config = BrowserConfig()
        assert config.headless is True
        assert config.viewport_width == 1280

    def test_headed_mode(self):
        with patch.dict(os.environ, {"LINEUP_HEADLESS": "false"}):
            config = BrowserConfig()
            assert config.headless is False


class TestExplorerConfig:
    def test_defaults(self):
        config = ExplorerConfig()
        assert config.max_depth == 3
        assert config.max_pages == 50
        assert "logout" in config.ignore_patterns


class TestScanConfig:
    def test_defaults(self):
        config = ScanConfig()
        assert config.provider == "ollama"
        assert config.max_test_cases == 20
        assert isinstance(config.ollama, OllamaConfig)
        assert isinstance(config.claude, ClaudeConfig)
        assert isinstance(config.gemini, GeminiConfig)

    def test_provider_env_override(self):
        with patch.dict(os.environ, {"LINEUP_PROVIDER": "claude"}):
            config = ScanConfig()
            assert config.provider == "claude"
