#!/usr/bin/env bash
# build_dev.sh — Create a dev build, tagging the current branch with an
# auto-incremented -alpha.# suffix if not already on a tag.
set -euo pipefail

# ─── Error trap ───────────────────────────────────────────────────────────────
trap 'echo "Error: build_dev.sh failed on line $LINENO." >&2' ERR

# ─── 0. Locate the add-on package directory ──────────────────────────────────
#   One add-on per repo: the package dir is whichever top-level folder
#   contains blender_manifest.toml. This means this script needs zero edits
#   when copied into a new project (see ../AGENTS.md section 2).
package_dir="$(dirname "$(find . -maxdepth 2 -name blender_manifest.toml -print -quit)")"
if [[ -z "$package_dir" || "$package_dir" == "." ]]; then
  echo "Error: no blender_manifest.toml found under any top-level folder." >&2
  exit 1
fi
package_dir="${package_dir#./}"
echo "Package dir: $package_dir"

# ─── 1. Detect current position ───────────────────────────────────────────────
#   git rev-parse --abbrev-ref HEAD returns "HEAD" when in detached-HEAD state
#   (i.e. already checked out a tag), and the branch name otherwise.
branch="$(git rev-parse --abbrev-ref HEAD)"
echo "Current ref: $branch"

# ─── 2. Check whether HEAD is already sitting on an exact tag ─────────────────
tag="$(git describe --exact-match --tags HEAD 2>/dev/null || true)"

if [[ -n "$tag" ]]; then
  echo "HEAD is already at tag '$tag' — skipping tag creation."
else
  # Find the most recent plain semver tag (vX.Y.Z, no pre-release suffix)
  # reachable anywhere in the repo, sorted by version.
  base_tag="$(
    git tag --list \
      | grep -E '^v?[0-9]+\.[0-9]+\.[0-9]+$' \
      | sort -V \
      | tail -1 \
      || true
  )"

  if [[ -z "$base_tag" ]]; then
    echo "Error: no base semver tag found to derive an alpha tag from." >&2
    exit 1
  fi

  echo "Base version tag: $base_tag"

  # Find the highest existing alpha number for this base tag.
  highest=0
  while IFS= read -r t; do
    if [[ "$t" =~ -alpha\.([0-9]+)$ ]]; then
      n="${BASH_REMATCH[1]}"
      (( n > highest )) && highest=$n
    fi
  done < <(git tag --list "${base_tag}-alpha.*" | sort -V)

  next=$(( highest + 1 ))
  tag="${base_tag}-alpha.${next}"

  echo "Creating local tag: $tag"
  git tag "$tag"
fi

# ─── 3. Switch to the tag (detached HEAD) ────────────────────────────────────
echo "Switching to tag: $tag"
git checkout "$tag"

# ─── 4. Resolve semver and run the build ─────────────────────────────────────
tag_desc="$(git describe --tags --abbrev=0 2>/dev/null || true)"

if [[ $tag_desc =~ ([0-9]+)\.([0-9]+)\.([0-9]+) ]]; then
  semver="${BASH_REMATCH[0]}"
  printf '%s -> %s\n' "$tag_desc" "$semver"
else
  echo "No semver found in: $tag_desc" >&2
  exit 1
fi

python3 scripts/bump_version.py "$semver"
python3 scripts/strip_dev_blocks.py

echo "Building Blender add-on with version: $semver..."
mkdir -p dist
blender --command extension build --source-dir "$package_dir" --output-dir dist

echo "Built Blender add-on with version: $semver"
