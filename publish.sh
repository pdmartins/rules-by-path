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
# WHERE THE LOCAL INSTALL COMES FROM. The mode decides, and the script repoints
# the marketplace accordingly: a release installs from GitHub — exactly what it
# just published, exactly what a user would get — while --local installs from
# this directory, which is the only way to run code that is not released yet.
# The marketplace name never changes, so the install id never changes either and
# the two can never both be installed.
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

# owner/repo, read from the remote rather than hardcoded, so renaming the
# repository does not need an edit here.
REMOTE_SLUG=$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null \
  | sed -E 's#^git@[^:]+:##; s#^https?://[^/]+/##; s#\.git$##' || true)

# ─── where the local install comes from ───────────────────────────────────────
# One marketplace name, two needs, so the MODE picks the source: a release
# installs what was actually published (GitHub, whose default branch this script
# points at main), while --local installs the working tree you are editing.
# Because the name is the same either way, the install id stays the same too, so
# the two can never coexist and the hook is never registered twice.
current_marketplace_source() {
  RBP_MARKETPLACE="$MARKETPLACE_NAME" python3 - <<'PYEOF'
import json, os
name = os.environ["RBP_MARKETPLACE"]
try:
    with open(os.path.expanduser("~/.claude/settings.json")) as handle:
        data = json.load(handle)
except OSError:
    raise SystemExit
source = ((data.get("extraKnownMarketplaces") or {}).get(name) or {}).get("source") or {}
kind = source.get("source", "")
where = source.get("repo") or source.get("path") or source.get("url") or ""
if kind:
    print(f"{kind}:{where}")
PYEOF
}

ensure_marketplace() {  # $1 = "github:owner/repo" or "directory:/path"
  local want="$1" have
  have=$(current_marketplace_source)
  if [ "$have" = "$want" ]; then
    # Already the right source; fetch so a release pushed seconds ago is visible.
    claude plugin marketplace update "$MARKETPLACE_NAME" >/dev/null 2>&1 || true
    return 0
  fi
  if [ -n "$have" ]; then
    info "marketplace '$MARKETPLACE_NAME' points at ${have%%:*}; repointing at ${want%%:*}"
    claude plugin marketplace remove "$MARKETPLACE_NAME" >/dev/null 2>&1 || true
  fi
  claude plugin marketplace add "${want#*:}"
}

# Uninstall + install rather than `claude plugin update`: update compares
# declared versions, and this project deliberately keeps the version fixed
# between releases, so an update would report "already at the latest version"
# and keep serving a stale cache.
refresh_local_install() {  # $1 = source spec, as ensure_marketplace takes it
  info "Refreshing the local install of $INSTALL_ID from ${1%%:*} (${1#*:})..."
  export RBP_INSTALL_ID="$INSTALL_ID"
  # Uninstall first: the marketplace cannot be repointed underneath a live
  # install without leaving the registry pointing at a cache nobody owns.
  claude plugin uninstall "$INSTALL_ID" --scope user >/dev/null 2>&1 || true
  ensure_marketplace "$1" || return 1
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
    dry "claude plugin uninstall $INSTALL_ID --scope user"
    dry "point marketplace '$MARKETPLACE_NAME' at directory $REPO_DIR (if it is not already)"
    dry "claude plugin install $INSTALL_ID --scope user"
    warn "Dry-run finished — nothing was executed."
    exit 0
  fi
  refresh_local_install "directory:$REPO_DIR" \
    || error "the local refresh failed — see the output above"
  success "Local install refreshed from the working tree (version $CURRENT_VERSION)."
  echo ""
  echo "  Hooks and commands load at session start: open a NEW session to pick this up."
  exit 0
fi

# ─── release gates ────────────────────────────────────────────────────────────
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
[ "$CURRENT_BRANCH" = "$RELEASE_BRANCH" ] && \
  error "already on $RELEASE_BRANCH. Release from $DEV_BRANCH."

# Getting back to the branch you started on must not depend on the happy path.
# The release checks out main to merge, and `set -e` means a conflicted merge or
# a rejected push kills the script right there — leaving you on main, mid-merge,
# without saying so. This trap runs on every exit, success or failure.
ORIGINAL_BRANCH="$CURRENT_BRANCH"
restore_branch() {
  local status=$?
  local current
  current=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)
  { [ -z "$current" ] || [ "$current" = "$ORIGINAL_BRANCH" ]; } && return $status
  if [ -f "$(git rev-parse --git-dir 2>/dev/null)/MERGE_HEAD" ]; then
    # Never abort it silently: an unfinished merge is the user's to resolve,
    # and throwing it away could discard conflict resolution already done.
    warn "a merge is still in progress on '$current' — finish it, or run:"
    warn "    git merge --abort && git checkout $ORIGINAL_BRANCH"
    return $status
  fi
  info "returning to $ORIGINAL_BRANCH (was left on $current)"
  git checkout "$ORIGINAL_BRANCH" >/dev/null 2>&1 || \
    warn "could not switch back — you are on '$current'; run: git checkout $ORIGINAL_BRANCH"
  return $status
}
trap restore_branch EXIT
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
    dry "reinstall $INSTALL_ID from github:${REMOTE_SLUG:-<none>} — i.e. from what"
    dry "  was just published, not from this working tree"
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
# Tolerant on purpose: the release is public from the line above, so nothing
# after it may abort the script. The EXIT trap is the backstop if this fails.
git checkout "$CURRENT_BRANCH" || warn "could not return to $CURRENT_BRANCH"
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
  if [ -n "$REMOTE_SLUG" ]; then
    RELEASE_SOURCE="github:$REMOTE_SLUG"
  else
    warn "no git remote — falling back to installing the working tree"
    RELEASE_SOURCE="directory:$REPO_DIR"
  fi
  refresh_local_install "$RELEASE_SOURCE" \
    || warn "v$NEW_VERSION IS published — only the local refresh failed"
fi

echo ""
success "Released v$NEW_VERSION"
# Checked after the fact, never as a gate: the version number is computed here,
# so nobody can write the section before knowing what to call it.
grep -q "^## $NEW_VERSION\b" CHANGELOG.md 2>/dev/null || \
  warn "CHANGELOG.md has no '## $NEW_VERSION' section yet — add one"
echo ""
echo "  Users install it with:"
echo "    /plugin marketplace add ${REMOTE_SLUG:-<owner/repo>}"
echo "    /plugin install $INSTALL_ID"
echo ""
echo "  Open a NEW session here to load the released build."
