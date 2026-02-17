# Index Build Tools

Scripts for building and distributing pre-built index packages.

## Overview

The `build_index.py` script extracts text from PDF files, builds a searchable SQLite index with FTS5, and packages it for distribution via GitHub Releases.

## Usage

### Basic Build

Build an index package from PDFs in the default `data/agreements` directory:

```bash
python tools/build_index.py --version 1.0.0
```

This creates:
- `dist/index-v1.0.0.zip` - The packaged index
- `dist/index-v1.0.0.zip.sha256` - SHA256 checksum file

### Custom Directories

```bash
python tools/build_index.py \
    --version 1.0.0 \
    --agreements-dir /path/to/pdfs \
    --output-dir /path/to/output
```

### Publishing to GitHub Releases

To automatically upload to GitHub Releases:

```bash
export GITHUB_TOKEN=your_github_token
export GITHUB_REPOSITORY=owner/repo

python tools/build_index.py --version 1.0.0 --publish
```

Or specify the repo directly:

```bash
python tools/build_index.py \
    --version 1.0.0 \
    --publish \
    --repo owner/repo
```

### Dry Run (Testing)

Test the build process without actually extracting PDFs:

```bash
python tools/build_index.py --version 1.0.0 --dry-run
```

## Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `--version` | Version string for the package | From `GITHUB_REF` or `0.0.0` |
| `--agreements-dir` | Directory containing PDF files | `data/agreements` |
| `--output-dir` | Output directory for artifacts | `dist` |
| `--publish` | Upload to GitHub Releases | Disabled |
| `--repo` | GitHub repo (owner/repo) | From `GITHUB_REPOSITORY` |
| `--dry-run` | Skip PDF extraction (for testing) | Disabled |

## Asset Naming Convention

Index packages follow this naming convention:

```
index-v{version}.zip
index-v{version}.zip.sha256
```

Examples:
- `index-v1.0.0.zip`
- `index-v1.2.3.zip`
- `index-v2.0.0-beta.1.zip`

## Package Contents

The zip file contains:

```
index-v1.0.0.zip
├── index.sqlite      # SQLite database with FTS5 index
└── metadata.json     # Package metadata
```

### metadata.json Format

```json
{
  "version": "1.0.0",
  "format": "sqlite-fts5",
  "files": ["index.sqlite"]
}
```

### Database Schema

The `index.sqlite` database contains:

- **files** - Indexed PDF file records
- **pdf_pages** - Extracted page text
- **page_fts** - FTS5 full-text search index
- **metadata** - Index metadata (version, created_at)

## Manual Upload to GitHub Releases

If you prefer to upload manually instead of using `--publish`:

1. Build the index:
   ```bash
   python tools/build_index.py --version 1.0.0
   ```

2. Create a GitHub Release:
   - Go to your repo's Releases page
   - Click "Draft a new release"
   - Set tag to `v1.0.0`
   - Upload both files:
     - `dist/index-v1.0.0.zip`
     - `dist/index-v1.0.0.zip.sha256`
   - Publish the release

3. Or use the GitHub CLI:
   ```bash
   gh release create v1.0.0 \
       dist/index-v1.0.0.zip \
       dist/index-v1.0.0.zip.sha256 \
       --title "Index v1.0.0" \
       --notes "Pre-built index package"
   ```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Build Index

on:
  push:
    tags:
      - 'v*'

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Build and publish index
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          python tools/build_index.py --publish
```

## Versioning

Versions are determined in this order:

1. `--version` command line argument
2. `GITHUB_REF` environment variable (for CI/CD)
3. Default: `0.0.0`

When running in GitHub Actions with a tag trigger, the version is automatically extracted from the tag (e.g., `refs/tags/v1.0.0` → `1.0.0`).

## Troubleshooting

### "Agreements directory not found"

Ensure the `--agreements-dir` path exists and contains PDF files.

### "GITHUB_TOKEN required"

The `--publish` flag requires a GitHub token. Set it via:
```bash
export GITHUB_TOKEN=your_token
```

### "Asset already exists"

If you're re-publishing the same version, the existing assets won't be overwritten. Either:
- Delete the existing release first
- Use a new version number

## Security Notes

- Never commit `GITHUB_TOKEN` to version control
- Use GitHub Actions secrets for CI/CD
- The token needs `contents: write` permission for releases
