"""SQLite-backed learning store for Lineup.

Persists test execution history so that subsequent scans generate
better test cases. Scoped per domain — each target site has its
own selector stats, known bugs, and scan history.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlparse

from lineup.core.models import Bug, ScanReport, TestResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    domain      TEXT NOT NULL,
    target_url  TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    duration_s  REAL,
    tests_gen   INTEGER DEFAULT 0,
    tests_pass  INTEGER DEFAULT 0,
    tests_fail  INTEGER DEFAULT 0,
    bugs_found  INTEGER DEFAULT 0,
    model_used  TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS selector_stats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    domain      TEXT NOT NULL,
    page_url    TEXT NOT NULL,
    selector    TEXT NOT NULL,
    action_type TEXT NOT NULL,
    times_ok    INTEGER DEFAULT 0,
    times_fail  INTEGER DEFAULT 0,
    last_seen   TEXT NOT NULL,
    UNIQUE(domain, page_url, selector, action_type)
);

CREATE TABLE IF NOT EXISTS learned_bugs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    domain         TEXT NOT NULL,
    page_url       TEXT NOT NULL,
    bug_title      TEXT NOT NULL,
    severity       TEXT NOT NULL,
    description    TEXT DEFAULT '',
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL,
    times_seen     INTEGER DEFAULT 1,
    still_active   INTEGER DEFAULT 1,
    UNIQUE(domain, page_url, bug_title)
);

CREATE INDEX IF NOT EXISTS idx_selector_domain ON selector_stats(domain);
CREATE INDEX IF NOT EXISTS idx_bugs_domain ON learned_bugs(domain);
CREATE INDEX IF NOT EXISTS idx_history_domain ON scan_history(domain);
"""

# Limits for the learning context injected into the LLM prompt.
_MAX_GOOD_SELECTORS = 20
_MAX_BAD_SELECTORS = 10
_MAX_KNOWN_BUGS = 10


class LearningStore:
    """SQLite-backed learning store, scoped per domain."""

    def __init__(self, db_dir: str = "./lineup-output") -> None:
        os.makedirs(db_dir, exist_ok=True)
        db_path = os.path.join(db_dir, "lineup-history.db")
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def domain_from_url(url: str) -> str:
        """Extract netloc: 'http://localhost:3000/foo' -> 'localhost:3000'."""
        return urlparse(url).netloc

    @staticmethod
    def _normalize_page_url(url: str) -> str:
        """Strip query params and fragments for consistent matching."""
        parsed = urlparse(url)
        return parsed._replace(query="", fragment="").geturl().rstrip("/")

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Write methods (called after test execution)
    # ------------------------------------------------------------------

    def record_scan(self, report: ScanReport) -> None:
        """Record scan metadata."""
        domain = self.domain_from_url(report.target_url)
        self._conn.execute(
            """INSERT INTO scan_history
               (domain, target_url, started_at, duration_s,
                tests_gen, tests_pass, tests_fail, bugs_found, model_used)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                domain,
                report.target_url,
                report.timestamp.isoformat(),
                report.duration_seconds,
                report.test_cases_generated,
                report.test_cases_passed,
                report.test_cases_failed,
                len(report.bugs),
                report.model_used,
            ),
        )
        self._conn.commit()

    def record_results(self, domain: str, results: list[TestResult]) -> None:
        """Update selector_stats based on which actions passed/failed."""
        now = self._now_iso()

        for result in results:
            page_url = self._normalize_page_url(result.test_case.target_url)

            for i, action in enumerate(result.test_case.actions):
                if action.action in ("navigate", "assert", "wait"):
                    continue
                if not action.selector:
                    continue

                # If the test passed, all actions succeeded.
                # If it failed, we check whether this action was the failing step.
                failed_step = None
                if not result.passed and result.actual_behavior:
                    # actual_behavior format: "Action 'X' failed at step N: ..."
                    import re

                    m = re.search(r"failed at step (\d+)", result.actual_behavior)
                    if m:
                        failed_step = int(m.group(1)) - 1  # 0-indexed

                if result.passed or (failed_step is not None and i < failed_step):
                    ok_inc, fail_inc = 1, 0
                elif failed_step is not None and i == failed_step:
                    ok_inc, fail_inc = 0, 1
                else:
                    continue  # Steps after the failure weren't executed

                self._conn.execute(
                    """INSERT INTO selector_stats
                       (domain, page_url, selector, action_type, times_ok, times_fail, last_seen)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(domain, page_url, selector, action_type) DO UPDATE SET
                           times_ok = times_ok + ?,
                           times_fail = times_fail + ?,
                           last_seen = ?""",
                    (
                        domain, page_url, action.selector, action.action,
                        ok_inc, fail_inc, now,
                        ok_inc, fail_inc, now,
                    ),
                )

        self._conn.commit()

    def record_bugs(self, domain: str, bugs: list[Bug]) -> None:
        """Upsert bugs. Increments times_seen for known bugs."""
        now = self._now_iso()

        for bug in bugs:
            page_url = self._normalize_page_url(bug.url)
            self._conn.execute(
                """INSERT INTO learned_bugs
                   (domain, page_url, bug_title, severity, description,
                    first_seen, last_seen, times_seen, still_active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1)
                   ON CONFLICT(domain, page_url, bug_title) DO UPDATE SET
                       severity = ?,
                       last_seen = ?,
                       times_seen = times_seen + 1,
                       still_active = 1""",
                (
                    domain, page_url, bug.title, bug.severity.value, bug.description,
                    now, now,
                    bug.severity.value, now,
                ),
            )

        self._conn.commit()

    def mark_fixed_bugs(self, domain: str, active_bug_titles: set[str]) -> None:
        """Mark bugs that didn't reproduce in this scan as potentially fixed."""
        if not active_bug_titles:
            self._conn.execute(
                "UPDATE learned_bugs SET still_active = 0 WHERE domain = ? AND still_active = 1",
                (domain,),
            )
        else:
            placeholders = ",".join("?" for _ in active_bug_titles)
            self._conn.execute(
                f"""UPDATE learned_bugs SET still_active = 0
                    WHERE domain = ? AND still_active = 1
                    AND bug_title NOT IN ({placeholders})""",
                (domain, *active_bug_titles),
            )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Read methods (called before test generation)
    # ------------------------------------------------------------------

    def get_scan_count(self, domain: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) as c FROM scan_history WHERE domain = ?", (domain,)
        ).fetchone()
        return row["c"] if row else 0

    def get_good_selectors(self, domain: str, page_url: str) -> list[dict]:
        """Selectors that have succeeded at least once, sorted by reliability."""
        page_url = self._normalize_page_url(page_url)
        rows = self._conn.execute(
            """SELECT selector, action_type, times_ok, times_fail
               FROM selector_stats
               WHERE domain = ? AND page_url = ? AND times_ok > 0
               ORDER BY CAST(times_ok AS REAL) / (times_ok + times_fail) DESC
               LIMIT ?""",
            (domain, page_url, _MAX_GOOD_SELECTORS),
        ).fetchall()
        return [
            {
                "selector": r["selector"],
                "action": r["action_type"],
                "ok": r["times_ok"],
                "fail": r["times_fail"],
            }
            for r in rows
        ]

    def get_bad_selectors(self, domain: str, page_url: str) -> list[dict]:
        """Selectors that have ONLY failed (times_ok == 0)."""
        page_url = self._normalize_page_url(page_url)
        rows = self._conn.execute(
            """SELECT selector, action_type, times_fail
               FROM selector_stats
               WHERE domain = ? AND page_url = ? AND times_ok = 0 AND times_fail > 0
               ORDER BY times_fail DESC
               LIMIT ?""",
            (domain, page_url, _MAX_BAD_SELECTORS),
        ).fetchall()
        return [
            {"selector": r["selector"], "action": r["action_type"], "fail": r["times_fail"]}
            for r in rows
        ]

    def get_known_bugs(self, domain: str) -> list[dict]:
        """Active bugs for this domain."""
        rows = self._conn.execute(
            """SELECT bug_title, page_url, severity, times_seen
               FROM learned_bugs
               WHERE domain = ? AND still_active = 1
               ORDER BY times_seen DESC
               LIMIT ?""",
            (domain, _MAX_KNOWN_BUGS),
        ).fetchall()
        return [
            {
                "title": r["bug_title"],
                "page_url": r["page_url"],
                "severity": r["severity"],
                "times_seen": r["times_seen"],
            }
            for r in rows
        ]

    def build_learning_context(self, domain: str, page_url: str) -> str:
        """Build a text block ready to inject into the LLM prompt.

        Returns an empty string if there's no prior history.
        """
        scan_count = self.get_scan_count(domain)
        if scan_count == 0:
            return ""

        good = self.get_good_selectors(domain, page_url)
        bad = self.get_bad_selectors(domain, page_url)
        bugs = self.get_known_bugs(domain)

        if not good and not bad and not bugs:
            return ""

        lines = [f"\n=== LEARNING FROM {scan_count} PREVIOUS SCANS ===\n"]

        if good:
            lines.append("VERIFIED WORKING selectors on this page:")
            for s in good:
                total = s["ok"] + s["fail"]
                lines.append(f"- {s['selector']} ({s['action']}) — worked {s['ok']}/{total} times")
            lines.append("")

        if bad:
            lines.append("DO NOT USE these selectors (always failed):")
            for s in bad:
                lines.append(f"- {s['selector']} ({s['action']}) — failed {s['fail']} times")
            lines.append("")

        if bugs:
            lines.append("KNOWN BUGS already found (generate DIFFERENT tests):")
            for b in bugs:
                lines.append(
                    f"- \"{b['title']}\" ({b['severity']}, seen {b['times_seen']}x) on {b['page_url']}"
                )
            lines.append("")

        lines.append("Focus on UNTESTED areas and use verified selectors when possible.")
        return "\n".join(lines)
