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
    if set(root) != {"schema_version", "renderer", "assets"}:
        raise ContractError("manifest keys must be assets, renderer, and schema_version")
    renderer = _mapping(root["renderer"], "renderer")
    if set(renderer) != {"name", "command"}:
        raise ContractError("renderer keys must be command and name")
    raw_assets = root["assets"]
    if not isinstance(raw_assets, list):
        raise ContractError("manifest.assets must be an array")
    return Manifest(
        schema_version=_integer(root, "schema_version", "manifest"),
        renderer_name=_text(renderer, "name", "renderer"),
        renderer_command=_text(renderer, "command", "renderer"),
        assets=tuple(_asset(value, index) for index, value in enumerate(raw_assets)),
    )


def _validate_manifest(manifest: Manifest) -> None:
    if manifest.schema_version != 1:
        raise ContractError("manifest.schema_version must be 1")
    if (manifest.renderer_name, manifest.renderer_command) != EXPECTED_RENDERER:
        raise ContractError("manifest renderer does not match the approved command")
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


def _check_render(manifest: Manifest) -> None:
    _check(manifest)
    with tempfile.TemporaryDirectory(prefix="architecture-render-") as temporary:
        directory = Path(temporary)
        for asset in manifest.assets:
            rendered = directory / asset.png
            _render(DIRECTORY / asset.svg, rendered)
            _validate_png(asset, rendered, verify_digest=False)
            if rendered.read_bytes() != (DIRECTORY / asset.png).read_bytes():
                raise ContractError(f"{asset.png} is not a deterministic export of {asset.svg}")
    print("Architecture render parity: OK")


def _manifest_document(manifest: Manifest) -> dict[str, object]:
    return {
        "schema_version": manifest.schema_version,
        "renderer": {
            "name": manifest.renderer_name,
            "command": manifest.renderer_command,
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


def _regenerate(manifest: Manifest) -> None:
    _validate_manifest(manifest)
    for asset in manifest.assets:
        _validate_svg(asset, verify_digest=False)

    updated_assets: list[Asset] = []
    with tempfile.TemporaryDirectory(prefix=".architecture-render-", dir=DIRECTORY) as temporary:
        directory = Path(temporary)
        rendered_assets: dict[str, Path] = {}
        for asset in manifest.assets:
            rendered = directory / asset.png
            _render(DIRECTORY / asset.svg, rendered)
            _validate_png(asset, rendered, verify_digest=False)
            rendered_assets[asset.asset_id] = rendered

        for asset in manifest.assets:
            rendered = rendered_assets[asset.asset_id]
            rendered.replace(DIRECTORY / asset.png)
            updated_assets.append(
                replace(
                    asset,
                    svg_sha256=_sha256(DIRECTORY / asset.svg),
                    png_sha256=_sha256(DIRECTORY / asset.png),
                )
            )

    updated = replace(manifest, assets=tuple(updated_assets))
    MANIFEST_PATH.write_text(
        json.dumps(_manifest_document(updated), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    _check(updated)


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
