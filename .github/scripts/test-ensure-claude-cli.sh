#!/usr/bin/env bash
# What ensure-claude-cli.sh must hold to: a `claude` that runs is left alone
# (one install), one that does not is reinstalled from a fresh cache, and one
# that still does not run after that fails the step.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ensure="$script_dir/ensure-claude-cli.sh"
sandbox="$(mktemp -d "${TMPDIR:-/tmp}/ensure-claude.XXXXXX")"
trap 'rm -rf "$sandbox"' EXIT

# A stand-in `npm` on PATH: each `ci` writes a `claude` that works or not,
# taken in turn from $OUTCOMES, and records its arguments.
mkdir -p "$sandbox/bin"
cat > "$sandbox/bin/npm" <<'STUB'
#!/usr/bin/env bash
echo "npm $*" >> "$NPM_CALLS"
prefix="$3"
n=$(wc -l < "$NPM_CALLS")
outcome=$(sed -n "${n}p" "$OUTCOMES")
mkdir -p "$prefix/node_modules/.bin"
if [ "$outcome" = ok ]; then
  printf '#!/usr/bin/env bash\necho 2.1.282\n' > "$prefix/node_modules/.bin/claude"
else
  printf '\x7fELF-not-for-here' > "$prefix/node_modules/.bin/claude"
fi
chmod +x "$prefix/node_modules/.bin/claude"
STUB
chmod +x "$sandbox/bin/npm"

case_() {
  local name="$1" outcomes="$2" want_status="$3" want_calls="$4"
  local dir="$sandbox/$name"; mkdir -p "$dir/prefix"
  printf '%s\n' $outcomes > "$dir/outcomes"; : > "$dir/calls"
  set +e
  PATH="$sandbox/bin:$PATH" NPM_CALLS="$dir/calls" OUTCOMES="$dir/outcomes" RUNNER_TEMP="$dir" \
    bash "$ensure" "$dir/prefix" > "$dir/out" 2>&1
  local status=$?
  set -e
  local calls; calls=$(wc -l < "$dir/calls")
  # want_status "fail" = any non-zero: the step fails with the shell's own error
  local ok_status=1
  if [ "$want_status" = fail ]; then [ "$status" -ne 0 ] || ok_status=0; else [ "$status" -eq "$want_status" ] || ok_status=0; fi
  if [ "$ok_status" -ne 1 ] || [ "$calls" -ne "$want_calls" ]; then
    echo "FAIL $name: status=$status (want $want_status) npm calls=$calls (want $want_calls)"; cat "$dir/out"; exit 1
  fi
  echo "ok   $name"
}

case_ works-first-time "ok" 0 1
case_ broken-then-fixed "bad ok" 0 2
case_ broken-twice "bad bad" fail 2
grep -q "Exec format error" "$sandbox/broken-twice/out" || { echo "FAIL the second failure did not show why"; exit 1; }
grep -q -- "--prefer-online --cache" "$sandbox/broken-then-fixed/calls" || { echo "FAIL the retry did not use a fresh cache"; exit 1; }
echo "ok   the retry installs from a fresh cache"
