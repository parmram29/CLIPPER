"""Measure how well the model's virality scores agree with your own ratings.

Workflow: ``clipper review`` writes a sheet; you fill the ``my_rating`` column (1-10) for the
clips you have judged; ``clipper eval review.csv`` reports how closely the model's
``virality_score`` ranking matches yours. Re-run it after changing a prompt to see whether the
agreement went up or down.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

# Fewer rated clips than this and the numbers are too noisy to trust (a rule of thumb).
MIN_RELIABLE = 20


@dataclass(frozen=True)
class Rated:
    title: str
    category: str
    model_score: float
    my_rating: float


@dataclass
class Report:
    n: int
    spearman: float | None
    k: int
    good: float
    precision_at_k: float | None
    base_rate: float | None
    lift: float | None
    mean_model_good: float | None
    mean_model_other: float | None
    by_category: list[tuple[str, int, float, float]] = field(default_factory=list)


def parse_number(text: str | None) -> float | None:
    """Read '8', '8.5' or '8/10' as a number; blank or unreadable gives None."""
    match = re.match(r"\s*(\d+(?:\.\d+)?)", text or "")
    return float(match.group(1)) if match else None


def load_rated(path: str | Path) -> list[Rated]:
    """Rows that have both a model score and your rating."""
    rated = []
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = {"virality_score", "my_rating"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"{path} is missing column(s): {', '.join(sorted(missing))}. "
                "Use a sheet made by `clipper review`."
            )
        for row in reader:
            model = parse_number(row.get("virality_score"))
            mine = parse_number(row.get("my_rating"))
            if model is None or mine is None:
                continue
            rated.append(Rated(row.get("title", ""), (row.get("category") or "").strip(), model, mine))
    return rated


def average_ranks(values: list[float]) -> list[float]:
    """1-based ranks, with ties sharing the average of the ranks they span."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation, or None when it is undefined (under 3 points, or no spread)."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = average_ranks(xs), average_ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    var_x = sum((a - mx) ** 2 for a in rx)
    var_y = sum((b - my) ** 2 for b in ry)
    if var_x == 0 or var_y == 0:
        return None
    return cov / (var_x * var_y) ** 0.5


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def evaluate(rated: list[Rated], *, good: float = 7.0, k: int = 3) -> Report:
    n = len(rated)
    report = Report(n, None, min(k, n), good, None, None, None, None, None)
    if n == 0:
        return report

    report.spearman = spearman([r.model_score for r in rated], [r.my_rating for r in rated])

    top = sorted(range(n), key=lambda i: (-rated[i].model_score, i))[: report.k]
    hits = sum(1 for i in top if rated[i].my_rating >= good)
    report.precision_at_k = hits / len(top)
    report.base_rate = sum(1 for r in rated if r.my_rating >= good) / n
    if report.base_rate:
        report.lift = report.precision_at_k / report.base_rate

    report.mean_model_good = _mean([r.model_score for r in rated if r.my_rating >= good])
    report.mean_model_other = _mean([r.model_score for r in rated if r.my_rating < good])

    categories: dict[str, list[Rated]] = {}
    for r in rated:
        categories.setdefault(r.category or "(none)", []).append(r)
    report.by_category = sorted(
        (
            (name, len(group), _mean([r.model_score for r in group]) or 0.0, _mean([r.my_rating for r in group]) or 0.0)
            for name, group in categories.items()
        ),
        key=lambda row: -row[1],
    )
    return report


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _num(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def format_report(report: Report) -> str:
    if report.n == 0:
        return (
            "No rated clips found. Fill the my_rating column (1-10) for some rows of the review "
            "sheet, then run this again."
        )
    lines = [
        f"Rated clips: {report.n}",
        "",
        f"Rank agreement (Spearman): {_num(report.spearman, 2)}",
        "   1 = the model ranks clips the way you do, 0 = unrelated, negative = opposite.",
        "",
        f"Top {report.k} by model score: {_pct(report.precision_at_k)} are clips you rated "
        f">= {report.good:g}",
        f"   Of all your rated clips, {_pct(report.base_rate)} are that good"
        + (f", so the model's top picks are x{report.lift:.1f} as likely to be good." if report.lift else "."),
        "",
        f"Mean model score: {_num(report.mean_model_good)} for clips you rated >= {report.good:g}, "
        f"{_num(report.mean_model_other)} for the rest",
    ]
    if report.by_category:
        lines += ["", "By category (clips / mean model score / mean of your ratings):"]
        lines += [f"   {name:<12} {count:>3}   {model:>4.1f}   {mine:>4.1f}" for name, count, model, mine in report.by_category]
    if report.n < MIN_RELIABLE:
        lines += [
            "",
            f"Only {report.n} rated clips: with fewer than about {MIN_RELIABLE} these numbers are "
            "noisy. Treat them as a rough guide.",
        ]
    return "\n".join(lines)
