#!/usr/bin/env bash
# cleanup_tags.sh — Delete local tags that do not exist on the remote.
#
# Usage:
#   ./cleanup_tags.sh              # interactive: lists orphaned tags, prompts before deleting
#   ./cleanup_tags.sh --dry-run    # list only, no deletions
#   ./cleanup_tags.sh --force      # delete without prompting
#   ./cleanup_tags.sh --alpha-only # scope to *-alpha.* tags only
set -euo pipefail

# ─── Error trap ───────────────────────────────────────────────────────────────
trap 'echo "Error: cleanup_tags.sh failed on line $LINENO." >&2' ERR

# ─── Defaults ─────────────────────────────────────────────────────────────────
dry_run=false
force=false
alpha_only=false
remote="${CLEANUP_TAGS_REMOTE:-origin}"

# ─── Argument parsing ─────────────────────────────────────────────────────────
for arg in "$@"; do
  case "$arg" in
    --dry-run)    dry_run=true ;;
    --force)      force=true ;;
    --alpha-only) alpha_only=true ;;
    --remote=*)   remote="${arg#--remote=}" ;;
    -h|--help)
      sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      echo "Usage: $0 [--dry-run] [--force] [--alpha-only] [--remote=<name>]" >&2
      exit 1
      ;;
  esac
done

if $dry_run && $force; then
  echo "Error: --dry-run and --force are mutually exclusive." >&2
  exit 1
fi

# ─── Verify remote is reachable ───────────────────────────────────────────────
if ! git remote get-url "$remote" &>/dev/null; then
  echo "Error: remote '$remote' is not configured." >&2
  exit 1
fi

echo "Fetching tag list from remote '$remote'..."
if ! remote_raw="$(git ls-remote --tags "$remote" 2>&1)"; then
  echo "Error: could not reach remote '$remote'." >&2
  echo "$remote_raw" >&2
  exit 1
fi

# ─── Build sets ───────────────────────────────────────────────────────────────
# Remote tags: strip peeled-ref entries (^{}) and the refs/tags/ prefix.
mapfile -t remote_tags < <(
  awk '{print $2}' <<< "$remote_raw" \
    | grep -v '\^{}' \
    | sed 's|refs/tags/||' \
    | sort
)

# Local tags, optionally filtered to -alpha.* only.
if $alpha_only; then
  mapfile -t local_tags < <(git tag --list '*-alpha.*' | sort)
  scope_label="alpha"
else
  mapfile -t local_tags < <(git tag --list | sort)
  scope_label="all"
fi

# ─── Find orphaned tags (local but not on remote) ─────────────────────────────
orphaned=()
for t in "${local_tags[@]}"; do
  # comm -23 would need files; a simple loop is fine for typical tag counts.
  found=false
  for r in "${remote_tags[@]}"; do
    [[ "$t" == "$r" ]] && { found=true; break; }
  done
  $found || orphaned+=("$t")
done

# ─── Report ───────────────────────────────────────────────────────────────────
if [[ ${#orphaned[@]} -eq 0 ]]; then
  echo "No unpushed local tags found (scope: $scope_label)."
  exit 0
fi

echo "Unpushed local tags (scope: $scope_label):"
printf '  %s\n' "${orphaned[@]}"
echo ""

# ─── Dry run stops here ───────────────────────────────────────────────────────
if $dry_run; then
  echo "Dry run: no tags were deleted."
  exit 0
fi

# ─── Confirm (skipped with --force) ──────────────────────────────────────────
if ! $force; then
  read -rp "Delete ${#orphaned[@]} tag(s)? [y/N] " confirm
  if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
  fi
fi

# ─── Delete ───────────────────────────────────────────────────────────────────
deleted=0
failed=0
for t in "${orphaned[@]}"; do
  if git tag -d "$t"; then
    (( ++deleted ))
  else
    echo "Warning: failed to delete tag '$t'." >&2
    (( ++failed ))
  fi
done

echo ""
echo "Deleted $deleted tag(s)${failed:+, $failed failed}."