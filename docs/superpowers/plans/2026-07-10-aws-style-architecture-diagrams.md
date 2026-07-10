# AWS-Style Architecture Diagrams Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Mermaid product and technical diagrams with polished, repository-native SVG diagrams inspired by the visual grammar of AWS architecture diagrams without implying AWS service dependencies.

**Architecture:** Store editable SVG source and PNG exports under `docs/assets/architecture/`. Embed the SVGs in Markdown, keep detailed textual contracts beside them, and validate XML structure, raster rendering, legibility, clipping, and documentation consistency.

**Tech Stack:** SVG 1.1, XML, Markdown, macOS `sips`, Python standard-library XML parsing, Git.

## Global Constraints

- All public text and labels are English.
- Use generic domain icons, never AWS service logos.
- Use a white background, dark navy structure, orange emphasis, neutral gray containers, and one blue accent for optional integrations.
- Keep the deterministic engine visually authoritative and the multi-agent council visually optional.
- The files must render directly on GitHub without JavaScript, Mermaid, external fonts, or remote assets.
- SVG labels must remain readable at 100 percent browser zoom and must not overlap, clip, or leave their containers.

---

### Task 1: Product Flow Asset

**Files:**
- Create: `docs/assets/architecture/product-flow.svg`
- Create: `docs/assets/architecture/product-flow.png`

**Interfaces:**
- Consumes: the journey and terminology in `docs/PRODUCT_FLOW.md`
- Produces: a standalone product-flow image embedded by `docs/PRODUCT_FLOW.md`

- [x] **Step 1: Draw the SVG source**

Create a 1600 by 1100 SVG with three horizontal bands: onboarding, forecast engine, and recurring result loop. Include the no-key quickstart, preset/custom setup, focus-team selection, optional live data, deterministic simulation, optional council, four output types, and the update-and-rerun loop.

- [x] **Step 2: Validate XML before rendering**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
from xml.etree import ElementTree

path = Path("docs/assets/architecture/product-flow.svg")
root = ElementTree.parse(path).getroot()
assert root.tag.endswith("svg")
assert root.attrib["viewBox"] == "0 0 1600 1100"
text = path.read_text(encoding="utf-8")
assert "mermaid" not in text.lower()
assert "Quickstart" in text
assert "Optional council" in text
PY
```

Expected: exit 0.

- [x] **Step 3: Render the PNG export**

Run:

```bash
sips -s format png docs/assets/architecture/product-flow.svg --out docs/assets/architecture/product-flow.png
```

Expected: a non-empty 1600 by 1100 PNG.

### Task 2: Technical Architecture Asset

**Files:**
- Create: `docs/assets/architecture/technical-architecture.svg`
- Create: `docs/assets/architecture/technical-architecture.png`

**Interfaces:**
- Consumes: ownership and failure contracts in `docs/ARCHITECTURE.md`
- Produces: a standalone architecture image embedded by `docs/ARCHITECTURE.md`

- [x] **Step 1: Draw the SVG source**

Create a 1920 by 1280 SVG with numbered flow steps and five bounded zones: entry and run context, data sources, validation boundary, deterministic engine, optional intelligence, and versioned outputs. Use solid arrows for authoritative data flow and dashed blue arrows for optional evidence. Show that the council receives legal opponents and bounds but cannot own completed results or tournament topology.

- [x] **Step 2: Validate XML and required contracts**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
from xml.etree import ElementTree

path = Path("docs/assets/architecture/technical-architecture.svg")
root = ElementTree.parse(path).getroot()
assert root.tag.endswith("svg")
assert root.attrib["viewBox"] == "0 0 1920 1280"
text = path.read_text(encoding="utf-8")
for phrase in (
    "Deterministic tournament engine",
    "Optional intelligence",
    "Locked results",
    "Versioned outputs",
):
    assert phrase in text
assert "mermaid" not in text.lower()
PY
```

Expected: exit 0.

- [x] **Step 3: Render the PNG export**

Run:

```bash
sips -s format png docs/assets/architecture/technical-architecture.svg --out docs/assets/architecture/technical-architecture.png
```

Expected: a non-empty 1920 by 1280 PNG.

### Task 3: Documentation Integration

**Files:**
- Modify: `README.md`
- Modify: `docs/PRODUCT_FLOW.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/superpowers/specs/2026-07-10-open-source-tournament-forecaster-design.md`

**Interfaces:**
- Consumes: the two SVG assets from Tasks 1 and 2
- Produces: GitHub-rendered documentation with no Mermaid dependency

- [x] **Step 1: Replace diagram source blocks with SVG embeds**

Use repository-relative Markdown image references:

```markdown
![Tournament Forecaster product flow](assets/architecture/product-flow.svg)
```

```markdown
![Tournament Forecaster technical architecture](assets/architecture/technical-architecture.svg)
```

Replace the two legacy Mermaid diagrams in `README.md` with the product and technical SVGs. Label them as target-state architecture until the migration gates pass.

- [x] **Step 2: Preserve the run sequence as a numbered table**

Replace the Mermaid sequence diagram with a seven-step table covering validation, optional refresh, locked-result loading, full simulation, optional council, forecast finalization, and atomic artifact output.

- [x] **Step 3: Update the release gate**

Change the public-release gate from Mermaid rendering to SVG and PNG consistency, legibility, and alignment with the implemented CLI and schemas.

### Task 4: Visual and Repository Verification

**Files:**
- Verify: `README.md`
- Verify: `docs/assets/architecture/*.svg`
- Verify: `docs/assets/architecture/*.png`
- Verify: `docs/PRODUCT_FLOW.md`
- Verify: `docs/ARCHITECTURE.md`

**Interfaces:**
- Consumes: all previous tasks
- Produces: a reviewable, committed documentation change

- [x] **Step 1: Check XML, image dimensions, and Markdown references**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
from xml.etree import ElementTree

for svg in Path("docs/assets/architecture").glob("*.svg"):
    ElementTree.parse(svg)
for doc in (Path("README.md"), Path("docs/PRODUCT_FLOW.md"), Path("docs/ARCHITECTURE.md")):
    text = doc.read_text(encoding="utf-8")
    assert "```mermaid" not in text
    for target in [part.split(")", 1)[0] for part in text.split("](")[1:]]:
        if target.endswith((".svg", ".png")):
            assert (doc.parent / target).exists(), target
PY
```

Expected: exit 0.

- [x] **Step 2: Inspect both PNGs at original resolution**

Confirm that all labels fit, arrows terminate cleanly, containers do not overlap, optional paths are visually distinct, and the diagrams remain understandable without the surrounding prose.

- [x] **Step 3: Check the repository diff**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors and only the planned documentation assets and files are changed.

- [x] **Step 4: Commit**

```bash
git add README.md docs/assets/architecture docs/PRODUCT_FLOW.md docs/ARCHITECTURE.md docs/superpowers/specs/2026-07-10-open-source-tournament-forecaster-design.md docs/superpowers/plans/2026-07-10-aws-style-architecture-diagrams.md
git commit -m "docs: replace Mermaid with AWS-style diagrams"
```
