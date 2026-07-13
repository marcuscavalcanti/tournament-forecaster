from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
ASSET_DIRECTORY = ROOT / "docs/assets/architecture"
EXPECTED_ASSETS = {
    "product-flow": ("product-flow.svg", "product-flow.png", 1600, 1100),
    "technical-architecture": (
        "technical-architecture.svg",
        "technical-architecture.png",
        1920,
        1280,
    ),
}


def test_readme_links_current_english_architecture_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    normalized = readme.casefold()

    for phrase in (
        "authoritative offline core",
        "future extension boundaries",
        "custom aws-style svg assets",
        "matching png exports",
        "not mermaid diagrams",
        "docs/assets/architecture/product-flow.svg",
        "docs/assets/architecture/product-flow.png",
        "docs/assets/architecture/technical-architecture.svg",
        "docs/assets/architecture/technical-architecture.png",
        "docs/assets/architecture/readme.md",
    ):
        assert phrase in normalized

    assert "target architecture diagrams" not in normalized


def test_architecture_manifest_approves_and_reproduces_svg_png_pairs() -> None:
    manifest_path = ASSET_DIRECTORY / "manifest.json"
    generator = ASSET_DIRECTORY / "generate.py"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == 1
    assert manifest["renderer"] == {
        "name": "macOS sips",
        "command": "sips -s format png {svg} --out {png}",
    }
    records = {record["id"]: record for record in manifest["assets"]}
    assert set(records) == set(EXPECTED_ASSETS)
    for asset_id, (svg, png, width, height) in EXPECTED_ASSETS.items():
        record = records[asset_id]
        assert record["svg"] == svg
        assert record["png"] == png
        assert record["width"] == width
        assert record["height"] == height
        assert len(record["svg_sha256"]) == 64
        assert len(record["png_sha256"]) == 64

    checked = subprocess.run(
        [sys.executable, str(generator), "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr

    if shutil.which("sips") is not None:
        rendered = subprocess.run(
            [sys.executable, str(generator), "--check-render"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        assert rendered.returncode == 0, rendered.stdout + rendered.stderr


def test_architecture_regeneration_does_not_partially_replace_assets(
    tmp_path: Path,
) -> None:
    copied_assets = tmp_path / "architecture"
    shutil.copytree(ASSET_DIRECTORY, copied_assets)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_sips = fake_bin / "sips"
    fake_sips.write_text(
        """
#!/usr/bin/env python3
import shutil
import sys
from pathlib import Path

source = Path(sys.argv[4])
output = Path(sys.argv[6])
if source.name == "technical-architecture.svg":
    raise SystemExit("injected render failure")
shutil.copyfile(source.with_suffix(".png"), output)
with output.open("ab") as stream:
    stream.write(b"changed")
""".lstrip(),
        encoding="utf-8",
    )
    fake_sips.chmod(0o755)

    protected = (
        copied_assets / "manifest.json",
        copied_assets / "product-flow.png",
        copied_assets / "technical-architecture.png",
    )
    before = {path.name: path.read_bytes() for path in protected}
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"

    completed = subprocess.run(
        [sys.executable, str(copied_assets / "generate.py"), "--regenerate"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 1
    assert "injected render failure" in completed.stderr
    assert {path.name: path.read_bytes() for path in protected} == before
