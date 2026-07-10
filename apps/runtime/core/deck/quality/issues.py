"""Validation issue model and deduplication helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class ValidationIssue:
    page: int
    severity: str
    kind: str
    description: str
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def issues_to_dicts(issues: list[ValidationIssue]) -> list[dict[str, Any]]:
    return [issue.to_dict() for issue in issues]


def dedupe_issues(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    seen: set[tuple[int, str, str]] = set()
    deduped: list[ValidationIssue] = []
    for issue in issues:
        key = (issue.page, issue.kind, issue.description)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


def dedupe_issue_dicts(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[int, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for issue in issues:
        key = (
            int(issue.get("page") or 0),
            str(issue.get("kind") or ""),
            str(issue.get("description") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped
