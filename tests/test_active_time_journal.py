"""Claude's active-time journal of the engine campaign is consistent and verifiable (SPEC.md, accounting and closure).

Append-only lines, ordered and non-overlapping intervals, credited seconds never above the observed interval minus the
excluded periods inside it, and a total never above the 24 h budget. Offline: reads the committed file only."""

import json
from datetime import datetime
from pathlib import Path

JOURNAL = Path(__file__).resolve().parents[1] / "docs" / "evaluation" / "active-time" / "claude.jsonl"
BUDGET_S = 24 * 3600


def _t(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")


def test_claude_active_time_journal_is_consistent():
    rows = [json.loads(line) for line in JOURNAL.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows, "journal vide"
    previous_end = None
    total = 0
    for row in rows:
        assert row["agent"] == "claude" and row["schema"] == "active-time-v1" and row["session"].startswith("session_")
        assert row["task"].strip() and isinstance(row["evidence"], dict) and row["evidence"]
        start, end = _t(row["start_utc"]), _t(row["end_utc"])
        assert start <= end
        if previous_end is not None:
            assert previous_end <= start, "intervalles chevauchants"
        previous_end = end
        excluded = 0
        for period in row["excluded"]:
            a, b = max(_t(period["from"]), start), min(_t(period["to"]), end)
            excluded += max(0, (b - a).total_seconds())
            assert period["reason"].strip()
        span = (end - start).total_seconds()
        assert 0 <= row["credited_seconds"] == int(span - excluded) <= span
        total += row["credited_seconds"]
    assert total <= BUDGET_S
