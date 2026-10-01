#!/usr/bin/env bash
# What ensure-apt.sh must hold to: it does not open apt when the packages are
# already there (that is the whole point — an apt call it does not make is a
# lock it cannot lose), it does install what is missing, it waits out a lock
# held by an apt outside its own (the host's apt-daily) but fails at once on
# any other apt error, and it refuses an empty argument list rather than
# silently doing nothing.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ensure="$script_dir/ensure-apt.sh"
sandbox="$(mktemp -d "${TMPDIR:-/tmp}/ensure-apt.XXXXXX")"
trap 'rm -rf "$sandbox"' EXIT

# Stand-ins on PATH so the test never needs root and never touches real apt:
# `dpkg` answers from a list the test controls, `apt-get` records its call and
# fails the way the test tells it to, and `sudo`/`flock`/`sleep` only record or
# pass through.
mkdir -p "$sandbox/bin"
cat > "$sandbox/bin/dpkg" <<'STUB'
#!/usr/bin/env bash
[ "${1:-}" = "-s" ] || exit 1
grep -qx "$2" "$INSTALLED_LIST"
STUB
cat > "$sandbox/bin/apt-get" <<'STUB'
#!/usr/bin/env bash
echo "apt-get $*" >> "$APT_CALLS"
left="$(cat "$APT_LOCKED_CALLS" 2>/dev/null || echo 0)"
if [ "$left" -gt 0 ]; then
  echo $((left - 1)) > "$APT_LOCKED_CALLS"
  echo "E: Could not get lock /var/lib/apt/lists/lock. It is held by process 1 (apt-get)" >&2
  exit 100
fi
if [ -n "${APT_BROKEN:-}" ]; then
  echo "E: Unable to locate package $APT_BROKEN" >&2
  exit 100
fi
STUB
cat > "$sandbox/bin/sleep" <<'STUB'
#!/usr/bin/env bash
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
export APT_LOCKED_CALLS="$sandbox/apt-locked-calls"
export CHEESE_APT_LOCK="$sandbox/apt.lock"

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
grep -q "apt-get .*update" "$APT_CALLS" || fail "did not refresh the package lists"
grep -q "apt-get .*install .*openssl" "$APT_CALLS" || fail "did not install the missing package"
grep -q "apt-get .*install .*tmux" "$APT_CALLS" && fail "reinstalled a package that was already there"

# 3. Another apt holds the lists lock for a while → wait, then install.
printf 'tmux\n' > "$INSTALLED_LIST"
: > "$APT_CALLS"
echo 2 > "$APT_LOCKED_CALLS"
"$ensure" tmux openssl > /dev/null || fail "gave up while another apt held the lock"
[ "$(grep -c 'apt-get .*update' "$APT_CALLS")" -eq 3 ] || fail "did not retry the update after a lock refusal"
grep -q "apt-get .*install .*openssl" "$APT_CALLS" || fail "did not install after the lock was released"

# 4. Any other apt failure → fail at once, no retry.
: > "$APT_CALLS"
echo 0 > "$APT_LOCKED_CALLS"
if APT_BROKEN=openssl "$ensure" tmux openssl > /dev/null 2>&1; then fail "hid an apt failure"; fi
[ "$(grep -c 'apt-get' "$APT_CALLS")" -eq 1 ] || fail "retried a failure that was not a lock"

# 5. No arguments → a usage error, not a silent success.
: > "$APT_CALLS"
if "$ensure" > /dev/null 2>&1; then fail "accepted an empty package list"; fi

echo "test-ensure-apt: ok"
