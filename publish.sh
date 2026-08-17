#!/usr/bin/env bash
# shellcheck shell=bash
# publish.sh — release rules-by-path, or just refresh this machine's install.
#
# VERSIONING POLICY. The version is MAJOR.MINOR.REVISION and it changes ONLY on
# a release, i.e. only when develop is merged into main. Between releases the
# version in develop equals the last published one; `0.0.0` means never
# published. There are no `-beta` suffixes: a pre-release suffix would only
# exist to make `claude plugin update` notice a change, and `--local` below
# solves that properly by reinstalling instead of comparing version strings.
#
# Usage:
#   bash publish.sh --local              refresh this machine's install from the
#                                        working tree. No git, no version change.
#                                        This is what you want during development.
#   bash publish.sh --minor              release: 0.3.1 -> 0.4.0   (default)
#   bash publish.sh --major              release: 0.4.0 -> 1.0.0
#   bash publish.sh --revision           release: 1.0.0 -> 1.0.1
#   bash publish.sh --dry-run            print the plan, execute nothing
#   bash publish.sh --yes                skip the confirmation prompt
#   bash publish.sh --keep-local         release without touching the local install
#
# A release merges into main and pushes it. That is not undoable from here, so
# everything that can refuse — branch, clean tree, tests, manifests — refuses
# BEFORE the first push, and nothing after it is allowed to abort the script.

set -euo pipefail

# ─── user-visible text and settings ───────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
TAG="rules-by-path"
RELEASE_BRANCH="main"
DEV_BRANCH="develop"

info()    { echo -e "${BLUE}[$TAG]${NC} $*"; }
success() { echo -e "${GREEN}[$TAG]${NC} ✅ $*"; }
warn()    { echo -e "${YELLOW}[$TAG]${NC} ⚠️  $*"; }
error()   { echo -e "${RED}[$TAG]${NC} ❌ $*"; exit 1; }
dry()     { echo -e "${YELLOW}[dry-run]${NC} $*"; }

# ─── arguments ────────────────────────────────────────────────────────────────
DRY_RUN=false
LOCAL_ONLY=false
KEEP_LOCAL=false
ASSUME_YES=false
BUMP="minor"

for arg in "$@"; do
  case "$arg" in
    --dry-run)    DRY_RUN=true ;;
    --local)      LOCAL_ONLY=true ;;
    --keep-local) KEEP_LOCAL=true ;;
    --yes|-y)     ASSUME_YES=true ;;
    --major)      BUMP="major" ;;
    --minor)      BUMP="minor" ;;
    --revision|--patch) BUMP="revision" ;;
    *)            error "unknown argument: $arg (see the header of this script)" ;;
  esac
done

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

# The repository root is the marketplace plus the development scaffolding; the
# plugin itself is one directory below, and that directory is exactly what
# `claude plugin install` copies.
PLUGIN_DIR="$REPO_DIR/plugins/rules-by-path"
PLUGIN_JSON="$PLUGIN_DIR/.claude-plugin/plugin.json"
MARKETPLACE_JSON="$REPO_DIR/.claude-plugin/marketplace.json"

read_json() { python3 -c "import json,sys; print(json.load(open('$1'))$2)"; }

PLUGIN_NAME=$(read_json "$PLUGIN_JSON" "['name']")
MARKETPLACE_NAME=$(read_json "$MARKETPLACE_JSON" "['name']")
INSTALL_ID="${PLUGIN_NAME}@${MARKETPLACE_NAME}"
CURRENT_VERSION=$(read_json "$PLUGIN_JSON" "['version']")

# ─── refreshing this machine's install ────────────────────────────────────────
# Shared by --local and by the tail of a release. Uninstall + install rather
# than `claude plugin update`: update compares declared versions, and this
# project deliberately keeps the version fixed between releases, so an update
# would report "already at the latest version" and keep serving a stale cache.
refresh_local_install() {
  info "Refreshing the local install of $INSTALL_ID..."
  export RBP_INSTALL_ID="$INSTALL_ID"
  if ! claude plugin marketplace update "$MARKETPLACE_NAME" >/dev/null 2>&1; then
    warn "marketplace '$MARKETPLACE_NAME' not registered here — adding $REPO_DIR"
    claude plugin marketplace add "$REPO_DIR" || return 1
  fi
  # An install may legitimately be absent; only the install step decides success.
  claude plugin uninstall "$INSTALL_ID" --scope user >/dev/null 2>&1 || true
  claude plugin install "$INSTALL_ID" --scope user || return 1

  local install_path
  install_path=$(python3 - <<'PYEOF'
import json, os
registry = os.path.expanduser("~/.claude/plugins/installed_plugins.json")
try:
    with open(registry) as handle:
        data = json.load(handle)
except OSError:
    raise SystemExit
for key, entries in (data.get("plugins") or {}).items():
    if key == os.environ["RBP_INSTALL_ID"]:
        for entry in entries:
            if entry.get("scope") == "user":
                print(entry.get("installPath", ""))
PYEOF
)
  [ -n "$install_path" ] && info "installed at: $install_path"
  return 0
}

# ─── --local: no git, no version change ───────────────────────────────────────
if $LOCAL_ONLY; then
  if $DRY_RUN; then
    dry "claude plugin marketplace update $MARKETPLACE_NAME  (or 'add $REPO_DIR')"
    dry "claude plugin uninstall $INSTALL_ID --scope user"
    dry "claude plugin install $INSTALL_ID --scope user"
    warn "Dry-run finished — nothing was executed."
    exit 0
  fi
  refresh_local_install || error "the local refresh failed — see the output above"
  success "Local install refreshed from the working tree (version $CURRENT_VERSION)."
  echo ""
  echo "  Hooks and commands load at session start: open a NEW session to pick this up."
  exit 0
fi

# ─── release gates ────────────────────────────────────────────────────────────
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
[ "$CURRENT_BRANCH" = "$RELEASE_BRANCH" ] && \
  error "already on $RELEASE_BRANCH. Release from $DEV_BRANCH."
[ "$CURRENT_BRANCH" = "$DEV_BRANCH" ] || \
  warn "releasing from '$CURRENT_BRANCH', not '$DEV_BRANCH'"

git diff --quiet && git diff --cached --quiet || \
  error "the working tree has uncommitted changes. Commit them before releasing."

MARKETPLACE_VERSION=$(python3 - <<PYEOF
import json
data = json.load(open("$MARKETPLACE_JSON"))
for plugin in data.get("plugins", []):
    if plugin.get("name") == "$PLUGIN_NAME":
        print(plugin.get("version", ""))
PYEOF
)
[ "$MARKETPLACE_VERSION" = "$CURRENT_VERSION" ] || \
  error "version mismatch: plugin.json says $CURRENT_VERSION, marketplace.json says $MARKETPLACE_VERSION"

info "Running the test suite..."
TEST_OUT=$(python3 -m unittest discover -s tests -q 2>&1) || {
  echo "$TEST_OUT" | tail -25
  error "tests failed — release aborted."
}
success "tests OK ($(echo "$TEST_OUT" | grep -E '^Ran ' | tail -1))"

info "Validating the plugin manifests..."
claude plugin validate . --strict >/dev/null 2>&1 || \
  error "'claude plugin validate . --strict' failed — release aborted."
success "manifests OK"

# ─── compute the new version ──────────────────────────────────────────────────
MAJOR=$(echo "$CURRENT_VERSION" | cut -d. -f1)
MINOR=$(echo "$CURRENT_VERSION" | cut -d. -f2)
REVISION=$(echo "$CURRENT_VERSION" | cut -d. -f3)
case "$BUMP" in
  major)    NEW_VERSION="$((MAJOR + 1)).0.0" ;;
  minor)    NEW_VERSION="${MAJOR}.$((MINOR + 1)).0" ;;
  revision) NEW_VERSION="${MAJOR}.${MINOR}.$((REVISION + 1))" ;;
esac

REMOTE_SLUG=$(git remote get-url origin 2>/dev/null \
  | sed -E 's#^git@[^:]+:##; s#^https?://[^/]+/##; s#\.git$##' || true)

echo ""
info "Plugin:      $INSTALL_ID"
info "Branch:      $CURRENT_BRANCH → $RELEASE_BRANCH"
info "Version:     $CURRENT_VERSION → $NEW_VERSION  ($BUMP)"
info "Remote:      ${REMOTE_SLUG:-<none>}"
echo ""

if $DRY_RUN; then
  dry "bump $CURRENT_VERSION → $NEW_VERSION in plugin.json and marketplace.json"
  dry "git commit -am 'release: v$NEW_VERSION' && git push origin $CURRENT_BRANCH"
  dry "git checkout $RELEASE_BRANCH  (created from $CURRENT_BRANCH if absent)"
  dry "git merge --no-ff $CURRENT_BRANCH -m 'release: v$NEW_VERSION'"
  dry "git push origin $RELEASE_BRANCH && git checkout $CURRENT_BRANCH"
  dry "gh repo edit $REMOTE_SLUG --default-branch $RELEASE_BRANCH"
  if $KEEP_LOCAL; then
    dry "(--keep-local) local install left untouched"
  else
    dry "refresh the local install (uninstall + install $INSTALL_ID)"
  fi
  warn "Dry-run finished — nothing was executed."
  exit 0
fi

if ! $ASSUME_YES; then
  printf "Merge %s into %s and push v%s? [y/N] " "$CURRENT_BRANCH" "$RELEASE_BRANCH" "$NEW_VERSION"
  read -r reply
  case "$reply" in [yY]*) ;; *) error "aborted." ;; esac
fi

# ─── bump, commit, merge, push ────────────────────────────────────────────────
RBP_NEW_VERSION="$NEW_VERSION" RBP_PLUGIN_NAME="$PLUGIN_NAME" python3 - <<PYEOF
import json, os

version = os.environ["RBP_NEW_VERSION"]
plugin_name = os.environ["RBP_PLUGIN_NAME"]

def rewrite(path, mutate):
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    mutate(data)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

def set_plugin_version(data):
    data["version"] = version

def set_marketplace_version(data):
    for plugin in data.get("plugins", []):
        if plugin.get("name") == plugin_name:
            plugin["version"] = version

rewrite("$PLUGIN_JSON", set_plugin_version)
rewrite("$MARKETPLACE_JSON", set_marketplace_version)
PYEOF
success "manifests bumped to $NEW_VERSION"

claude plugin validate . --strict >/dev/null 2>&1 || \
  error "the bumped manifests do not validate — nothing was pushed, fix and retry."

git commit -am "release: v$NEW_VERSION"
git push origin "$CURRENT_BRANCH"
success "committed and pushed on $CURRENT_BRANCH"

info "Merging $CURRENT_BRANCH → $RELEASE_BRANCH..."
if git show-ref --verify --quiet "refs/heads/$RELEASE_BRANCH"; then
  git checkout "$RELEASE_BRANCH"
  git merge --no-ff "$CURRENT_BRANCH" -m "release: v$NEW_VERSION"
else
  git checkout -b "$RELEASE_BRANCH"   # first release: main starts here
fi
git push origin "$RELEASE_BRANCH"
git checkout "$CURRENT_BRANCH"
success "v$NEW_VERSION is on $RELEASE_BRANCH"

# ─── nothing below may abort: the release is already public ───────────────────
# `/plugin marketplace add owner/repo` reads the repository's DEFAULT branch, so
# a default branch still pointing at develop would hand users the development
# code — the exact opposite of what this release just did.
if [ -n "$REMOTE_SLUG" ] && command -v gh >/dev/null 2>&1; then
  DEFAULT_BRANCH=$(gh repo view "$REMOTE_SLUG" --json defaultBranchRef \
    --jq .defaultBranchRef.name 2>/dev/null || true)
  if [ "$DEFAULT_BRANCH" != "$RELEASE_BRANCH" ]; then
    info "GitHub default branch is '$DEFAULT_BRANCH' — switching it to $RELEASE_BRANCH..."
    gh repo edit "$REMOTE_SLUG" --default-branch "$RELEASE_BRANCH" \
      && success "default branch is now $RELEASE_BRANCH" \
      || warn "could not switch it — run: gh repo edit $REMOTE_SLUG --default-branch $RELEASE_BRANCH"
  fi
  VISIBILITY=$(gh repo view "$REMOTE_SLUG" --json visibility --jq .visibility 2>/dev/null || true)
  [ "$VISIBILITY" = "PRIVATE" ] && \
    warn "the repository is still PRIVATE — nobody can install it until you make it public"
fi

if $KEEP_LOCAL; then
  info "--keep-local — this machine's install was left untouched"
else
  refresh_local_install || warn "v$NEW_VERSION IS published — only the local refresh failed"
fi

echo ""
success "Released v$NEW_VERSION"
echo ""
echo "  Users install it with:"
echo "    /plugin marketplace add ${REMOTE_SLUG:-<owner/repo>}"
echo "    /plugin install $INSTALL_ID"
echo ""
echo "  Open a NEW session here to load the released build."
