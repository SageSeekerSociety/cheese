#!/usr/bin/env bash
# What ensure-apt.sh must hold to: it does not open apt when the packages are
# already there (that is the whole point — an apt call it does not make is a
# lock it cannot lose), it does install what is missing, and it refuses an
# empty argument list rather than silently doing nothing.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ensure="$script_dir/ensure-apt.sh"
sandbox="$(mktemp -d "${TMPDIR:-/tmp}/ensure-apt.XXXXXX")"
trap 'rm -rf "$sandbox"' EXIT

# Stand-ins on PATH so the test never needs root and never touches real apt:
# `dpkg` answers from a list the test controls, and `apt-get`/`sudo`/`flock`
# only record that they were called.
mkdir -p "$sandbox/bin"
cat > "$sandbox/bin/dpkg" <<'STUB'
#!/usr/bin/env bash
[ "${1:-}" = "-s" ] || exit 1
grep -qx "$2" "$INSTALLED_LIST"
STUB
cat > "$sandbox/bin/apt-get" <<'STUB'
#!/usr/bin/env bash
echo "apt-get $*" >> "$APT_CALLS"
STUB
cat > "$sandbox/bin/sudo" <<'STUB'
#!/usr/bin/env bash
exec "$@"
STUB
cat > "$sandbox/bin/flock" <<'STUB'
#!/usr/bin/env bash
echo "flock $1" >> "$APT_CALLS"
shift
exec "$@"
STUB
chmod +x "$sandbox/bin/"*

export PATH="$sandbox/bin:$PATH"
export INSTALLED_LIST="$sandbox/installed"
export APT_CALLS="$sandbox/apt-calls"

fail() { echo "FAIL: $1" >&2; exit 1; }

# 1. Everything present → apt is never called.
printf 'tmux\nopenssl\n' > "$INSTALLED_LIST"
: > "$APT_CALLS"
out="$("$ensure" tmux openssl)"
[ -s "$APT_CALLS" ] && fail "opened apt although both packages were installed"
grep -q "already installed" <<<"$out" || fail "did not report the packages as present"

# 2. One missing → apt runs, under the lock, for the missing one only.
printf 'tmux\n' > "$INSTALLED_LIST"
: > "$APT_CALLS"
"$ensure" tmux openssl > /dev/null
grep -q "^flock " "$APT_CALLS" || fail "installed without taking the lock"
grep -q "apt-get update" "$APT_CALLS" || fail "did not refresh the package lists"
grep -q "apt-get install .*openssl" "$APT_CALLS" || fail "did not install the missing package"
grep -q "apt-get install .*tmux" "$APT_CALLS" && fail "reinstalled a package that was already there"

# 3. No arguments → a usage error, not a silent success.
: > "$APT_CALLS"
if "$ensure" > /dev/null 2>&1; then fail "accepted an empty package list"; fi

echo "test-ensure-apt: ok"
