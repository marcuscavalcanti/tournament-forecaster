"""Atomic JSON, Markdown, and SVG forecast report bundles."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..atomic_io import atomic_write_text
from ..domain import Forecast
from .bracket_svg import render_bracket_svg
from .json_report import render_json_report
from .markdown_report import render_markdown_report


@dataclass(frozen=True, slots=True)
class ReportPaths:
    """Filesystem paths produced by a complete forecast report bundle."""

    json: Path
    markdown: Path
    svg: Path

    def __iter__(self) -> Iterator[Path]:
        return iter((self.json, self.markdown, self.svg))


@dataclass(frozen=True, slots=True)
class RenderedReportPaths:
    """Filesystem paths produced by rendering an existing forecast."""

    markdown: Path
    svg: Path

    def __iter__(self) -> Iterator[Path]:
        return iter((self.markdown, self.svg))


def _replace_staged_file(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _transactional_write(destination: Path, files: dict[str, str]) -> None:
    if destination.exists() and not destination.is_dir():
        raise ValueError("output path conflicts with an existing file")
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".tournament-forecast-stage-",
        dir=destination.parent,
    ) as temporary_name:
        staging = Path(temporary_name)
        for name, content in files.items():
            atomic_write_text(staging / name, content)

        targets = {name: destination / name for name in files}
        backups = {
            name: destination / f".{name}.backup"
            for name, target in targets.items()
            if target.exists()
        }
        committed: set[str] = set()
        try:
            for name, backup in backups.items():
                os.replace(targets[name], backup)
            for name, target in targets.items():
                _replace_staged_file(staging / name, target)
                committed.add(name)
        except BaseException:
            for name in committed:
                targets[name].unlink(missing_ok=True)
            for name, backup in backups.items():
                if backup.exists():
                    targets[name].unlink(missing_ok=True)
                    os.replace(backup, targets[name])
            for name, target in targets.items():
                if name not in backups:
                    target.unlink(missing_ok=True)
            raise
        finally:
            for backup in backups.values():
                backup.unlink(missing_ok=True)


def write_report_bundle(forecast: Forecast, destination: Path) -> ReportPaths:
    """Atomically replace a coherent JSON, Markdown, and SVG report set."""

    _transactional_write(
        destination,
        {
            "forecast.json": render_json_report(forecast),
            "report.md": render_markdown_report(forecast),
            "bracket.svg": render_bracket_svg(forecast),
        },
    )
    return ReportPaths(
        json=destination / "forecast.json",
        markdown=destination / "report.md",
        svg=destination / "bracket.svg",
    )


def write_rendered_reports(
    forecast: Forecast,
    destination: Path,
) -> RenderedReportPaths:
    """Atomically render Markdown and SVG from an existing forecast."""

    _transactional_write(
        destination,
        {
            "report.md": render_markdown_report(forecast),
            "bracket.svg": render_bracket_svg(forecast),
        },
    )
    return RenderedReportPaths(
        markdown=destination / "report.md",
        svg=destination / "bracket.svg",
    )


__all__ = [
    "RenderedReportPaths",
    "ReportPaths",
    "render_bracket_svg",
    "render_json_report",
    "render_markdown_report",
    "write_rendered_reports",
    "write_report_bundle",
]
