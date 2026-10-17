#!/usr/bin/env bash
# Clone with path-scoped credentials, checking access before creating anything.
# Usage: scratch-clone.sh [--source WORKTREE] [--helper HELPER] [--read-only]
#                        [--repo OWNER/REPO] DESTINATION
# Defaults: this worktree's origin, and receive-pack access (preparing to push).
# --repo selects another repository on the SAME host; --read-only checks fetch
# access only, useful for a fork whose owner will apply a bundle/patch themselves.
# Discovery is not a promise that branch protection or a later push will allow
# an update. This script never pushes and never installs hooks.
set -eu
exec python3 -I - "$0" "$@" <<'PY'
import argparse
import base64
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

script = Path(sys.argv[1]).resolve()
parser = argparse.ArgumentParser(prog=script.name, description="Clone with checked, path-scoped Git credentials.")
parser.add_argument("--source", default=str(script.parent.parent.parent), metavar="WORKTREE")
parser.add_argument("--helper", help="explicit Git credential helper instead of inherited helpers")
parser.add_argument("--repo", help="OWNER/REPO on the source origin's host (default: origin)")
parser.add_argument("--read-only", action="store_true", help="check fetch, not push-service access")
parser.add_argument("destination")
args = parser.parse_args(sys.argv[2:])
source = Path(args.source).resolve()
destination = Path(args.destination).absolute()


def fail(message):
    print(f"scratch-clone: {message}", file=sys.stderr)
    raise SystemExit(1)


# Do not inherit tracing or repository/config overrides into a fresh clone.
env = {k: v for k, v in os.environ.items()
       if not k.startswith(("GIT_TRACE", "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
       and k not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                     "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CURL_VERBOSE",
                     "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS"}}
env.update(GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="/bin/false")


def git(*command, cwd=source, input=None, isolated=False, timeout=60):
    run_env = dict(env)
    if isolated:
        run_env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    try:
        return subprocess.run(["git", *command], cwd=cwd, env=run_env, input=input,
                              text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        fail(f"Git could not complete within {timeout}s; check the source worktree and connectivity.")


if not source.is_dir() or git("rev-parse", "--show-toplevel").returncode:
    fail("--source must name an existing worktree; rerun with --source /path/to/worktree.")
if destination.exists() or destination.is_symlink():
    fail("destination already exists; choose a new destination (nothing was removed).")
if not destination.parent.is_dir():
    fail("destination parent is missing; create it with mkdir -p, then rerun.")
origin = git("config", "--get", "remote.origin.url")
if origin.returncode:
    fail("source has no origin; set it with git -C WORKTREE remote add origin HTTPS_URL.")
try:
    url = urllib.parse.urlsplit(origin.stdout.strip())
    _ = url.port  # Validate the port before consulting a credential helper.
except ValueError:
    fail("invalid origin URL; use git -C WORKTREE remote set-url origin HTTPS_URL.")
# Plain HTTP is restricted to literal loopback addresses for local Git servers.
local_http = url.scheme == "http" and url.hostname in {"127.0.0.1", "::1"}
if (url.scheme != "https" and not local_http) or not url.hostname or url.username or url.password or url.query or url.fragment:
    fail("origin must be a credential-free HTTPS URL; use git -C WORKTREE remote set-url origin HTTPS_URL.")
repo = args.repo if args.repo is not None else url.path.removeprefix("/").removesuffix(".git")
if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", repo) or repo.split("/")[1] in {".", ".."} or repo.endswith(".git"):
    fail("repository path must be OWNER/REPO without .git; rerun with --repo OWNER/REPO.")
# Default cloning preserves origin scopes; --repo always names OWNER/REPO.git.
path = repo + (".git" if args.repo is not None or url.path.endswith(".git") else "")
remote = f"{url.scheme}://{url.netloc}/{path}"

# Keep order, empty resets, and URL-specific entries: Git, not this script,
# chooses which helpers match the target. Never copy hooks or URL rewrites.
config = git("config", "--null", "--get-regexp", r"^credential\.")
if config.returncode not in (0, 1):
    fail("cannot read source credential configuration; check git -C WORKTREE config --list.")
settings = [("credential.helper", "")]
for entry in config.stdout.split("\0"):
    if not entry:
        continue
    key, separator, value = entry.partition("\n")
    kind = key.rsplit(".", 1)[-1].lower()
    if kind not in {"helper", "username", "usehttppath"} or (kind == "helper" and args.helper is not None):
        continue
    if not separator:
        if kind != "usehttppath":
            fail("credential helper/username has no value; correct the source Git configuration or supply --helper.")
        value = "true"  # Git's implicit boolean differs from an explicitly empty value.
    settings.append((key, value))
if args.helper is not None:
    if not args.helper.strip():
        fail("--helper is empty; provide a credential helper, not a token.")
    settings.append(("credential.helper", args.helper))
settings.extend([(f"credential.{remote}.useHttpPath", "true"),
                 ("credential.useHttpPath", "true"), ("http.followRedirects", "false")])
options = [part for key, value in settings for part in ("-c", f"{key}={value}")]
with tempfile.TemporaryDirectory(prefix="scratch-credential-") as empty:
    # No source-local helper can re-enter the chain after an explicit override.
    credential = git(*options, "credential", "fill", cwd=empty,
                     input=f"url={remote}\n\n", isolated=True)
fields = dict(line.split("=", 1) for line in credential.stdout.splitlines() if "=" in line)
if credential.returncode or not fields.get("username") or not fields.get("password"):
    fail("no credentials for the requested repository path. Rerun with --helper '!cheese git-credential' "
         "in an authorized Cheese project, or --helper YOUR_AUTHORIZED_HELPER. "
         "For another owner's fork, have the owner apply your bundle/patch; do not retry with broader credentials.")
if (fields.get("protocol") != url.scheme or fields.get("host") != url.netloc
        or fields.get("path") != path):
    fail("credential helper returned a different repository path/host; correct --repo OWNER/REPO or --helper.")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# A public ls-remote (or a no-op dry-run push) is not evidence of authorization.
# Send these very credentials preemptively to the smart-HTTP service, without
# redirects. GET discovery changes no refs and sends no pack to the server.
service = "git-upload-pack" if args.read_only else "git-receive-pack"
authorization = base64.b64encode(f"{fields['username']}:{fields['password']}".encode()).decode()
request = urllib.request.Request(f"{remote}/info/refs?service={service}", headers={
    "Authorization": f"Basic {authorization}", "Cache-Control": "no-cache",
})
try:
    with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
        prefix = f"# service={service}\n".encode()
        expected = f"{len(prefix) + 4:04x}".encode() + prefix + b"0000"
        valid = (response.status == 200
                 and response.headers.get_content_type() == f"application/x-{service}-advertisement"
                 and response.read(len(expected)) == expected)
except urllib.error.HTTPError as exc:
    if exc.code in (401, 403, 404):
        fail(f"HTTP {exc.code}: credentials lack access to {repo} / {service}, or the path does not exist. "
             "Check --repo OWNER/REPO and --helper YOUR_AUTHORIZED_HELPER. "
             "If only fetching is intended, rerun with --read-only; for fork edits, deliver a bundle/patch to its owner.")
    fail(f"HTTP {exc.code}: discovery failed (redirects are refused). Correct the source origin URL, then rerun.")
except (OSError, ValueError, urllib.error.URLError):
    fail("authenticated discovery failed; check network/TLS and source origin, then rerun. No clone was created.")
if not valid:
    fail("remote did not advertise the requested Git service; check source origin and --repo OWNER/REPO.")
# Discard the token before cloning; Git asks the inherited helper when needed.
del fields, credential, authorization, request
clone_options = [part for key, value in settings for part in ("--config", f"{key}={value}")]
result = git("clone", *clone_options, "--", remote, str(destination),
             cwd=destination.parent, isolated=True, timeout=600)
if result.returncode:
    fail("clone failed after discovery; check connectivity and helper availability. "
         "Inspect the destination before retrying; no existing files were removed.")
print(f"Cloned {repo} to {destination}; {service} discovery accepted these credentials.")
if args.read_only:
    print("Read-only preflight: push permission was not checked. Give fork edits to its owner as a bundle/patch.")
else:
    print("Push-service access checked; branch rules and a later push can still refuse an update.")
print("Verify any later push against the remote SHA; this command has not pushed anything.")
PY
