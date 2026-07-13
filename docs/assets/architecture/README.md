# Architecture Asset Contract

The two SVG files in this directory are the editable sources for the public architecture diagrams. Their PNG files are deterministic exports for clients that cannot render SVG. The diagrams are custom AWS-style assets; they do not contain vendor logos or remote image dependencies.

`manifest.json` is the approved asset list. It records each source and export path, dimensions, renderer, and SHA-256 digest. A new image is not public merely because it has a neutral filename: it must be reviewed and added to the manifest.

## Verification

Run the portable manifest, structure, dimension, and digest checks on any supported platform:

```bash
python3 docs/assets/architecture/generate.py --check
```

On macOS, verify source-to-export parity by rendering both SVG files into a temporary directory and requiring byte-for-byte equality with the committed PNG files:

```bash
python3 docs/assets/architecture/generate.py --check-render
```

## Regeneration

Edit only the SVG source, then regenerate both PNG exports and refresh the manifest hashes with the repository's established renderer:

```bash
python3 docs/assets/architecture/generate.py --regenerate
```

The regeneration command requires macOS `sips`. Review the SVG text and rendered images before committing all source, export, and manifest changes together.
