"""Reject Portuguese prose on the English-first public repository surface."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).parents[1]
TEXT_SUFFIXES: Final = {".md", ".py", ".toml", ".txt", ".yaml", ".yml"}
PUBLIC_SINGLE_FILES: Final = {
    Path("README.md"),
    Path("LICENSE"),
    Path("NOTICE.md"),
    Path("SECURITY.md"),
    Path("CONTRIBUTING.md"),
    Path("CODE_OF_CONDUCT.md"),
    Path(".env.example"),
    Path(".gitignore"),
    Path("pyproject.toml"),
    Path("tests/test_tournament_forecast_cli.py"),
    Path("tests/test_clean_wheel.py"),
    Path("tests/test_public_repository_contract.py"),
}
PUBLIC_PREFIXES: Final = (
    "src/tournament_forecaster/",
    "tests/tournament_forecaster/",
    "tests/presets/",
    "tests/examples/",
    "examples/",
    "docs/",
    "presets/",
    ".github/",
)
EXEMPT_PREFIXES: Final = (
    "src/tournament_forecaster/compatibility/",
    "tests/tournament_forecaster/fixtures/",
    "docs/superpowers/",
)
EXEMPT_FILES: Final = {
    Path("tests/tournament_forecaster/test_legacy_compatibility.py"),
    Path("docs/knockout-stage-output-contract.md"),
}
LOCALIZED_KEYS: Final = {
    "aliases",
    "display_name",
    "team_display_names",
    "localized_display_names",
}
PORTUGUESE_WORDS: Final = {
    "agora",
    "ainda",
    "ambientes",
    "arquivo",
    "arquivos",
    "artefato",
    "artefatos",
    "atual",
    "busca",
    "configuracao",
    "configuração",
    "dados",
    "depois",
    "equipe",
    "equipes",
    "fase",
    "fases",
    "locais",
    "nao",
    "não",
    "para",
    "probabilidade",
    "concorrente",
    "rodar",
    "saida",
    "saída",
    "somente",
    "segredos",
    "sistema",
    "tambem",
    "também",
    "execução",
}
WORD_PATTERN: Final = re.compile(r"[^\W\d_]+", flags=re.UNICODE)


def _repository_files() -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return tuple(Path(item.decode()) for item in completed.stdout.split(b"\0") if item)


def _is_public(path: Path) -> bool:
    text = path.as_posix()
    if path in EXEMPT_FILES or text.startswith(EXEMPT_PREFIXES):
        return False
    return path in PUBLIC_SINGLE_FILES or text.startswith(PUBLIC_PREFIXES)


def _json_prose(value: object, *, key: str | None = None) -> list[str]:
    if key in LOCALIZED_KEYS:
        return []
    if isinstance(value, dict):
        prose: list[str] = []
        for child_key, child_value in value.items():
            prose.append(str(child_key))
            prose.extend(_json_prose(child_value, key=str(child_key)))
        return prose
    if isinstance(value, list):
        prose = []
        for item in value:
            prose.extend(_json_prose(item, key=key))
        return prose
    return [value] if isinstance(value, str) else []


def _content(path: Path) -> str | None:
    absolute = ROOT / path
    if path.suffix.casefold() == ".json":
        try:
            document = json.loads(absolute.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return "\n".join(_json_prose(document))
    if path.suffix.casefold() not in TEXT_SUFFIXES and path.name not in {
        "LICENSE",
        ".env.example",
        ".gitignore",
    }:
        return None
    try:
        return absolute.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def main() -> int:
    violations: list[str] = []
    for path in sorted(set(_repository_files())):
        if not _is_public(path):
            continue
        content = _content(path)
        if content is None:
            continue
        words = {word.casefold() for word in WORD_PATTERN.findall(content)}
        matches = sorted(words & PORTUGUESE_WORDS)
        if matches:
            violations.append(f"{path}: Portuguese words: {', '.join(matches)}")
        if not path.as_posix().startswith("tests/") and (
            "/Users/" in content or "C:\\Users\\" in content
        ):
            violations.append(f"{path}: personal absolute path")
    if violations:
        print("English public surface violations:")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print("English public surface: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
