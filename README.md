# lineup

Autonomous testing platform — POC

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- [Ollama](https://ollama.ai) running locally

## Quick Start

### 1. Install Ollama and pull a model

```bash
# macOS
brew install ollama
ollama serve  # in a separate terminal
ollama pull llama3.1:8b
```

### 2. Install Lineup

```bash
cd lineup-poc
uv sync
uv run playwright install chromium
```

### 3. Run a scan

```bash
# Basic scan
uv run lineup scan https://juice-shop.herokuapp.com

# With options
uv run lineup scan https://myapp.com --model llama3.1:8b --depth 2 --max-tests 10 --headed

# Check Ollama connection
uv run lineup check
```

## CLI Options

```
lineup scan <url>
  --model TEXT       Ollama model (default: llama3.1:8b)
  --depth INT        Max exploration depth (default: 3)
  --max-tests INT    Max test cases to generate (default: 20)
  --output TEXT      Output directory (default: ./lineup-output)
  --headed           Run browser in visible mode
  --ollama-url TEXT  Ollama API URL (default: http://localhost:11434)
```

## Environment Variables

```
LINEUP_OLLAMA_URL=http://localhost:11434
LINEUP_MODEL=llama3.1:8b
LINEUP_HEADLESS=true
LINEUP_OUTPUT_DIR=./lineup-output
```

## Project Structure

```
src/lineup/
├── core/
│   ├── interfaces.py   # Abstract contracts (Explorer, Generator, Executor, Reporter)
│   ├── models.py        # Domain models (TestCase, Bug, ScanReport, etc.)
│   └── config.py        # Configuration with env var overrides
├── explorer/
│   └── web.py           # Web crawler with Playwright
├── generator/
│   └── llm.py           # Test generation + bug analysis via Ollama
├── executor/
│   └── browser.py       # Test execution with Playwright
├── reporter/
│   └── html.py          # Self-contained HTML report generator
├── scan.py              # Scan orchestrator (explore → generate → execute → analyze → report)
└── cli.py               # CLI entry point
```

## Output

After a scan, `lineup-output/` contains:

- `lineup-report.html` — Full report with bugs, screenshots, and stats
- `screenshots/` — Screenshots taken during exploration and test execution
