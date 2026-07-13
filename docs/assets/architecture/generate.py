#!/usr/bin/env python3
"""Verify and regenerate approved architecture SVG/PNG asset pairs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import struct
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final
from xml.etree import ElementTree

DIRECTORY: Final = Path(__file__).resolve().parent
MANIFEST_PATH: Final = DIRECTORY / "manifest.json"
EXPECTED_ASSETS: Final = {
    "product-flow": ("product-flow.svg", "product-flow.png", 1600, 1100),
    "technical-architecture": (
        "technical-architecture.svg",
        "technical-architecture.png",
        1920,
        1280,
    ),
}
EXPECTED_RENDERER: Final = (
    "macOS sips",
    "sips -s format png {svg} --out {png}",
)
EXPECTED_PROVENANCE: Final = (
    "Project-authored architecture diagrams",
    "Marcus Cavalcanti",
    "MIT",
    "https://github.com/marcuscavalcanti/worldcup2026",
    "da8d4dfa116d88af4f1de0590e56c6bb1d8ffc6a",
)
PROTECTED_MARKS: Final = ("fifa", "uefa", "conmebol", "opta")
PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")


class ContractError(ValueError):
    """Raised when an architecture asset violates the public contract."""


@dataclass(frozen=True)
class Asset:
    asset_id: str
    svg: str
    png: str
    width: int
    height: int
    svg_sha256: str
    png_sha256: str


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    renderer_name: str
    renderer_command: str
    renderer_reference_version: str
    provenance_origin: str
    provenance_author: str
    provenance_license: str
    provenance_repository: str
    provenance_commit: str
    assets: tuple[Asset, ...]


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ContractError(f"{label} must be an object with string keys")
    return {str(key): item for key, item in value.items()}


def _text(mapping: dict[str, object], key: str, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise ContractError(f"{label}.{key} must be a string")
    return value


def _integer(mapping: dict[str, object], key: str, label: str) -> int:
    value = mapping.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContractError(f"{label}.{key} must be an integer")
    return value


def _asset(value: object, index: int) -> Asset:
    label = f"assets[{index}]"
    mapping = _mapping(value, label)
    expected_keys = {
        "id",
        "svg",
        "png",
        "width",
        "height",
        "svg_sha256",
        "png_sha256",
    }
    if set(mapping) != expected_keys:
        raise ContractError(f"{label} keys must be {sorted(expected_keys)}")
    return Asset(
        asset_id=_text(mapping, "id", label),
        svg=_text(mapping, "svg", label),
        png=_text(mapping, "png", label),
        width=_integer(mapping, "width", label),
        height=_integer(mapping, "height", label),
        svg_sha256=_text(mapping, "svg_sha256", label),
        png_sha256=_text(mapping, "png_sha256", label),
    )


def _load_manifest() -> Manifest:
    document: object = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    root = _mapping(document, "manifest")
    if set(root) != {"schema_version", "renderer", "provenance", "assets"}:
        raise ContractError(
            "manifest keys must be assets, provenance, renderer, and schema_version"
        )
    renderer = _mapping(root["renderer"], "renderer")
    if set(renderer) != {"name", "command", "reference_version"}:
        raise ContractError("renderer keys must be command, name, and reference_version")
    provenance = _mapping(root["provenance"], "provenance")
    expected_provenance_keys = {
        "origin",
        "author",
        "license",
        "source_repository",
        "source_commit",
    }
    if set(provenance) != expected_provenance_keys:
        raise ContractError(
            f"provenance keys must be {sorted(expected_provenance_keys)}"
        )
    raw_assets = root["assets"]
    if not isinstance(raw_assets, list):
        raise ContractError("manifest.assets must be an array")
    return Manifest(
        schema_version=_integer(root, "schema_version", "manifest"),
        renderer_name=_text(renderer, "name", "renderer"),
        renderer_command=_text(renderer, "command", "renderer"),
        renderer_reference_version=_text(renderer, "reference_version", "renderer"),
        provenance_origin=_text(provenance, "origin", "provenance"),
        provenance_author=_text(provenance, "author", "provenance"),
        provenance_license=_text(provenance, "license", "provenance"),
        provenance_repository=_text(provenance, "source_repository", "provenance"),
        provenance_commit=_text(provenance, "source_commit", "provenance"),
        assets=tuple(_asset(value, index) for index, value in enumerate(raw_assets)),
    )


def _validate_manifest(manifest: Manifest) -> None:
    if manifest.schema_version != 2:
        raise ContractError("manifest.schema_version must be 2")
    if (manifest.renderer_name, manifest.renderer_command) != EXPECTED_RENDERER:
        raise ContractError("manifest renderer does not match the approved command")
    if not manifest.renderer_reference_version.strip():
        raise ContractError("manifest renderer reference_version must not be empty")
    provenance = (
        manifest.provenance_origin,
        manifest.provenance_author,
        manifest.provenance_license,
        manifest.provenance_repository,
        manifest.provenance_commit,
    )
    if provenance != EXPECTED_PROVENANCE:
        raise ContractError("manifest provenance does not match the approved project source")
    observed = {
        asset.asset_id: (asset.svg, asset.png, asset.width, asset.height)
        for asset in manifest.assets
    }
    if len(observed) != len(manifest.assets) or observed != EXPECTED_ASSETS:
        raise ContractError("manifest assets do not match the approved architecture set")
    for asset in manifest.assets:
        if Path(asset.svg).name != asset.svg or Path(asset.png).name != asset.png:
            raise ContractError(f"{asset.asset_id} paths must remain inside the asset directory")
        if not SHA256_PATTERN.fullmatch(asset.svg_sha256):
            raise ContractError(f"{asset.asset_id} has an invalid SVG digest")
        if not SHA256_PATTERN.fullmatch(asset.png_sha256):
            raise ContractError(f"{asset.asset_id} has an invalid PNG digest")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_svg(asset: Asset, *, verify_digest: bool) -> None:
    path = DIRECTORY / asset.svg
    if verify_digest and _sha256(path) != asset.svg_sha256:
        raise ContractError(f"{asset.svg} does not match its approved digest")
    root = ElementTree.parse(path).getroot()
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise ContractError(f"{asset.svg} is not an SVG document")
    if root.get("width") != str(asset.width) or root.get("height") != str(asset.height):
        raise ContractError(f"{asset.svg} dimensions do not match the manifest")
    if root.get("viewBox") != f"0 0 {asset.width} {asset.height}":
        raise ContractError(f"{asset.svg} viewBox does not match the manifest")

    text = path.read_text(encoding="utf-8")
    for mark in PROTECTED_MARKS:
        if re.search(rf"\b{re.escape(mark)}\b", text, flags=re.IGNORECASE):
            raise ContractError(f"{asset.svg} contains the protected name {mark}")
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1].casefold()
        if local_name in {"foreignobject", "image", "script"}:
            raise ContractError(f"{asset.svg} contains prohibited element {local_name}")
        for attribute, value in element.attrib.items():
            if attribute.rsplit("}", 1)[-1] == "href" and not value.startswith("#"):
                raise ContractError(f"{asset.svg} contains an external reference")


def _png_dimensions(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if len(header) != 24 or header[:8] != PNG_SIGNATURE or header[12:16] != b"IHDR":
        raise ContractError(f"{path.name} is not a valid PNG")
    return struct.unpack(">II", header[16:24])


def _validate_png(asset: Asset, path: Path, *, verify_digest: bool) -> None:
    if _png_dimensions(path) != (asset.width, asset.height):
        raise ContractError(f"{path.name} dimensions do not match the manifest")
    if verify_digest and _sha256(path) != asset.png_sha256:
        raise ContractError(f"{path.name} does not match its approved digest")


def _check(manifest: Manifest) -> None:
    _validate_manifest(manifest)
    approved_files = {
        filename
        for asset in manifest.assets
        for filename in (asset.svg, asset.png)
    }
    actual_files = {
        path.name
        for pattern in ("*.svg", "*.png")
        for path in DIRECTORY.glob(pattern)
    }
    if actual_files != approved_files:
        raise ContractError("architecture image files do not match the approved manifest")
    for asset in manifest.assets:
        _validate_svg(asset, verify_digest=True)
        _validate_png(asset, DIRECTORY / asset.png, verify_digest=True)
    print("Architecture assets: OK")


def _sips() -> str:
    executable = shutil.which("sips")
    if executable is None:
        raise ContractError("macOS sips is required for rendering")
    return executable


def _render(svg: Path, png: Path) -> None:
    completed = subprocess.run(
        [_sips(), "-s", "format", "png", str(svg), "--out", str(png)],
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ContractError(f"sips could not render {svg.name}: {detail}")


def _renderer_version() -> str:
    completed = subprocess.run(
        [_sips(), "--version"],
        text=True,
        capture_output=True,
    )
    output = completed.stdout.strip() or completed.stderr.strip()
    if completed.returncode != 0 or not output:
        raise ContractError("sips did not report a renderer version")
    return output.splitlines()[0]


def _check_render(manifest: Manifest) -> None:
    _check(manifest)
    renderer_version = _renderer_version()
    with tempfile.TemporaryDirectory(prefix="architecture-render-") as temporary:
        directory = Path(temporary)
        for asset in manifest.assets:
            rendered = directory / asset.png
            _render(DIRECTORY / asset.svg, rendered)
            _validate_png(asset, rendered, verify_digest=False)
            if rendered.read_bytes() != (DIRECTORY / asset.png).read_bytes():
                raise ContractError(f"{asset.png} is not a deterministic export of {asset.svg}")
    print(
        "Architecture render parity: OK "
        f"({renderer_version}; reference {manifest.renderer_reference_version})"
    )


def _manifest_document(manifest: Manifest) -> dict[str, object]:
    return {
        "schema_version": manifest.schema_version,
        "renderer": {
            "name": manifest.renderer_name,
            "command": manifest.renderer_command,
            "reference_version": manifest.renderer_reference_version,
        },
        "provenance": {
            "origin": manifest.provenance_origin,
            "author": manifest.provenance_author,
            "license": manifest.provenance_license,
            "source_repository": manifest.provenance_repository,
            "source_commit": manifest.provenance_commit,
        },
        "assets": [
            {
                "id": asset.asset_id,
                "svg": asset.svg,
                "png": asset.png,
                "width": asset.width,
                "height": asset.height,
                "svg_sha256": asset.svg_sha256,
                "png_sha256": asset.png_sha256,
            }
            for asset in manifest.assets
        ],
    }


def _commit_file(source: Path, destination: Path) -> None:
    """Atomically replace one file within the regeneration transaction."""
    source.replace(destination)


def _regenerate(manifest: Manifest) -> None:
    _validate_manifest(manifest)
    for asset in manifest.assets:
        _validate_svg(asset, verify_digest=False)

    with tempfile.TemporaryDirectory(prefix=".architecture-render-", dir=DIRECTORY) as temporary:
        directory = Path(temporary)
        rendered_assets: dict[str, Path] = {}
        for asset in manifest.assets:
            rendered = directory / asset.png
            _render(DIRECTORY / asset.svg, rendered)
            _validate_png(asset, rendered, verify_digest=False)
            rendered_assets[asset.asset_id] = rendered

        updated_assets: list[Asset] = []
        for asset in manifest.assets:
            rendered = rendered_assets[asset.asset_id]
            updated_assets.append(
                replace(
                    asset,
                    svg_sha256=_sha256(DIRECTORY / asset.svg),
                    png_sha256=_sha256(rendered),
                )
            )

        updated = replace(
            manifest,
            renderer_reference_version=_renderer_version(),
            assets=tuple(updated_assets),
        )
        _validate_manifest(updated)
        manifest_candidate = directory / "manifest.json"
        manifest_candidate.write_text(
            json.dumps(_manifest_document(updated), indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )

        commit_plan = [
            (rendered_assets[asset.asset_id], DIRECTORY / asset.png)
            for asset in manifest.assets
        ]
        commit_plan.append((manifest_candidate, MANIFEST_PATH))
        rollback_directory = directory / "rollback"
        rollback_directory.mkdir()
        backups: list[tuple[Path, Path]] = []
        for index, (_, destination) in enumerate(commit_plan):
            backup = rollback_directory / f"{index}-{destination.name}"
            shutil.copy2(destination, backup)
            backups.append((backup, destination))

        try:
            for source, destination in commit_plan:
                _commit_file(source, destination)
            _check(updated)
        except BaseException as error:
            rollback_errors: list[str] = []
            for backup, destination in reversed(backups):
                try:
                    backup.replace(destination)
                except OSError as rollback_error:
                    rollback_errors.append(f"{destination.name}: {rollback_error}")
            if rollback_errors:
                details = "; ".join(rollback_errors)
                error.add_note(
                    f"architecture regeneration was not fully rolled back: {details}"
                )
            else:
                error.add_note("architecture regeneration was rolled back")
            raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="validate committed assets")
    mode.add_argument(
        "--check-render",
        action="store_true",
        help="render to a temporary directory and require byte parity",
    )
    mode.add_argument(
        "--regenerate",
        action="store_true",
        help="render committed PNG files and refresh manifest hashes",
    )
    arguments = parser.parse_args(argv)
    try:
        manifest = _load_manifest()
        if arguments.check_render:
            _check_render(manifest)
        elif arguments.regenerate:
            _regenerate(manifest)
        else:
            _check(manifest)
    except (ContractError, ElementTree.ParseError, json.JSONDecodeError, OSError) as error:
        parser.exit(1, f"architecture asset error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
