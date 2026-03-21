"""HTML report generator.

Produces a single-file HTML report with embedded styles,
bug details, screenshots, and scan statistics. No external
dependencies — works offline.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime

from lineup.core.interfaces import Reporter
from lineup.core.models import ScanReport, Severity


class HtmlReporter(Reporter):
    """Generates a self-contained HTML report."""

    async def generate_report(self, report: ScanReport, output_dir: str) -> str:
        """Generate an HTML report file."""
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "lineup-report.html")

        bugs_html = self._render_bugs(report)
        results_html = self._render_results(report)
        stats_html = self._render_stats(report)

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lineup Report — {report.target_url}</title>
<style>
  :root {{
    --cyan: #06B6D4; --blue: #3B82F6; --indigo: #6366F1;
    --violet: #8B5CF6; --green: #10B981; --red: #EF4444;
    --orange: #F59E0B; --dark: #0F172A; --muted: #94A3B8;
    --bg: #F8FAFC; --card: #FFFFFF; --border: #E2E8F0;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg); color: var(--dark); line-height: 1.6;
  }}
  .header {{
    background: var(--dark); color: white; padding: 2rem 3rem;
  }}
  .header h1 {{
    font-size: 1.5rem; font-weight: 300;
  }}
  .header h1 strong {{ font-weight: 800; }}
  .header .target {{ color: var(--muted); margin-top: 0.5rem; font-size: 0.9rem; }}
  .header .meta {{ color: var(--muted); font-size: 0.8rem; margin-top: 0.25rem; }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 2rem; }}
  .stats {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 1rem; margin-bottom: 2rem;
  }}
  .stat-card {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 8px; padding: 1.25rem; text-align: center;
  }}
  .stat-card .value {{ font-size: 2rem; font-weight: 700; }}
  .stat-card .label {{ font-size: 0.8rem; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; }}
  .stat-card.critical .value {{ color: var(--red); }}
  .stat-card.passed .value {{ color: var(--green); }}
  .stat-card.failed .value {{ color: var(--red); }}
  .section {{ margin-bottom: 2rem; }}
  .section h2 {{
    font-size: 1.2rem; font-weight: 700; margin-bottom: 1rem;
    padding-bottom: 0.5rem; border-bottom: 2px solid var(--border);
  }}
  .bug-card {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 8px; padding: 1.5rem; margin-bottom: 1rem;
    border-left: 4px solid var(--muted);
  }}
  .bug-card.critical {{ border-left-color: var(--red); }}
  .bug-card.high {{ border-left-color: var(--orange); }}
  .bug-card.medium {{ border-left-color: var(--blue); }}
  .bug-card.low {{ border-left-color: var(--cyan); }}
  .bug-card .title {{ font-weight: 700; font-size: 1rem; margin-bottom: 0.5rem; }}
  .bug-card .severity {{
    display: inline-block; font-size: 0.7rem; font-weight: 700;
    padding: 2px 8px; border-radius: 4px; text-transform: uppercase;
    letter-spacing: 1px; margin-bottom: 0.5rem;
  }}
  .severity.critical {{ background: #FEE2E2; color: #991B1B; }}
  .severity.high {{ background: #FEF3C7; color: #92400E; }}
  .severity.medium {{ background: #DBEAFE; color: #1E40AF; }}
  .severity.low {{ background: #CFFAFE; color: #155E75; }}
  .severity.info {{ background: #F1F5F9; color: #475569; }}
  .bug-card .description {{ color: #475569; margin-bottom: 0.75rem; }}
  .bug-card .field {{ margin-bottom: 0.5rem; }}
  .bug-card .field-label {{ font-weight: 600; font-size: 0.85rem; color: var(--dark); }}
  .bug-card .field-value {{ font-size: 0.85rem; color: #475569; }}
  .steps {{ list-style: decimal; padding-left: 1.25rem; }}
  .steps li {{ margin-bottom: 0.25rem; font-size: 0.85rem; color: #475569; }}
  .result-row {{
    display: flex; align-items: center; gap: 1rem;
    padding: 0.75rem 1rem; background: var(--card);
    border: 1px solid var(--border); border-radius: 6px;
    margin-bottom: 0.5rem; font-size: 0.85rem;
  }}
  .result-row .status {{
    font-weight: 700; font-size: 0.75rem;
    padding: 2px 8px; border-radius: 4px;
    text-transform: uppercase; letter-spacing: 0.5px;
    white-space: nowrap;
  }}
  .status.pass {{ background: #D1FAE5; color: #065F46; }}
  .status.fail {{ background: #FEE2E2; color: #991B1B; }}
  .result-row .name {{ font-weight: 600; flex: 1; }}
  .result-row .duration {{ color: var(--muted); white-space: nowrap; }}
  .screenshot {{
    max-width: 100%; border: 1px solid var(--border);
    border-radius: 4px; margin-top: 0.5rem;
  }}
  .footer {{
    text-align: center; padding: 2rem; color: var(--muted);
    font-size: 0.8rem; border-top: 1px solid var(--border);
  }}
</style>
</head>
<body>
<div class="header">
  <h1>line<strong>up</strong></h1>
  <div class="target">{report.target_url}</div>
  <div class="meta">{report.timestamp.strftime('%Y-%m-%d %H:%M')} &middot; {report.duration_seconds:.1f}s &middot; {report.model_used}</div>
</div>
<div class="container">
  {stats_html}
  {bugs_html}
  {results_html}
</div>
<div class="footer">
  Generated by Lineup v0.1.0 &middot; Autonomous Testing Platform
</div>
</body>
</html>"""

        with open(output_path, "w") as f:
            f.write(html)

        return output_path

    def _render_stats(self, report: ScanReport) -> str:
        critical_count = sum(1 for b in report.bugs if b.severity == Severity.CRITICAL)
        high_count = sum(1 for b in report.bugs if b.severity == Severity.HIGH)

        return f"""<div class="stats">
  <div class="stat-card"><div class="value">{len(report.app_map.routes)}</div><div class="label">Pages Scanned</div></div>
  <div class="stat-card"><div class="value">{report.app_map.total_elements}</div><div class="label">Elements Found</div></div>
  <div class="stat-card"><div class="value">{report.test_cases_generated}</div><div class="label">Tests Generated</div></div>
  <div class="stat-card passed"><div class="value">{report.test_cases_passed}</div><div class="label">Passed</div></div>
  <div class="stat-card failed"><div class="value">{report.test_cases_failed}</div><div class="label">Failed</div></div>
  <div class="stat-card critical"><div class="value">{len(report.bugs)}</div><div class="label">Bugs Found</div></div>
</div>"""

    def _render_bugs(self, report: ScanReport) -> str:
        if not report.bugs:
            return '<div class="section"><h2>No Bugs Found</h2><p>All tests passed.</p></div>'

        cards = []
        for bug in sorted(report.bugs, key=lambda b: list(Severity).index(b.severity)):
            steps = ""
            if bug.steps_to_reproduce:
                items = "".join(f"<li>{step}</li>" for step in bug.steps_to_reproduce)
                steps = f'<div class="field"><span class="field-label">Steps to Reproduce:</span><ol class="steps">{items}</ol></div>'

            screenshots = ""
            if bug.screenshots:
                for sp in bug.screenshots[:2]:
                    if os.path.exists(sp):
                        with open(sp, "rb") as f:
                            b64 = base64.b64encode(f.read()).decode()
                        screenshots += f'<img class="screenshot" src="data:image/png;base64,{b64}" />'

            cards.append(f"""<div class="bug-card {bug.severity.value}">
  <span class="severity {bug.severity.value}">{bug.severity.value}</span>
  <div class="title">{bug.title}</div>
  <div class="description">{bug.description}</div>
  <div class="field"><span class="field-label">URL:</span> <span class="field-value">{bug.url}</span></div>
  <div class="field"><span class="field-label">Expected:</span> <span class="field-value">{bug.expected}</span></div>
  <div class="field"><span class="field-label">Actual:</span> <span class="field-value">{bug.actual}</span></div>
  {steps}
  {screenshots}
</div>""")

        return f'<div class="section"><h2>Bugs ({len(report.bugs)})</h2>{"".join(cards)}</div>'

    def _render_results(self, report: ScanReport) -> str:
        rows = []
        for r in report.results:
            status_class = "pass" if r.passed else "fail"
            status_text = "PASS" if r.passed else "FAIL"
            rows.append(f"""<div class="result-row">
  <span class="status {status_class}">{status_text}</span>
  <span class="name">{r.test_case.name}</span>
  <span class="duration">{r.duration_seconds:.1f}s</span>
</div>""")

        return f'<div class="section"><h2>Test Results ({len(report.results)})</h2>{"".join(rows)}</div>'
