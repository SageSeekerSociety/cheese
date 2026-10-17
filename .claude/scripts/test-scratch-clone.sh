#!/usr/bin/env bash
# Self-test for scratch-clone.sh.  Usage: bash test-scratch-clone.sh [TARGET]
# TARGET defaults to scratch-clone.sh next to this file; pass a mutated copy to
# check the suite goes red. Stdlib only; hermetic loopback smart-HTTP server,
# throwaway bare repos and fake helpers; HOME/XDG/git config isolated.
set -eu
exec python3 -I - "$0" "$@" <<'PY'
import base64, http.server, os, shutil, subprocess, sys, tempfile, threading, urllib.parse
from pathlib import Path

TEST = Path(sys.argv[1]).resolve()
TARGET = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else TEST.parent / "scratch-clone.sh"
if not TARGET.is_file():
    sys.exit(f"error: target script not found: {TARGET}")

WORK = Path(tempfile.mkdtemp(prefix="scratch-clone-selftest-"))
HOME = WORK / "home"
(HOME / ".config").mkdir(parents=True)
HELPERS = WORK / "helpers"
HELPERS.mkdir()

# Isolated git environment: drop repo overrides and one-shot PARAMETERS so the
# clone and the CGI backend never inherit the caller's GIT_DIR/WORK_TREE.
SKIP = {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
        "GIT_CURL_VERBOSE"}
GITENV = {k: v for k, v in os.environ.items() if k not in SKIP}
GITENV.update({
    "HOME": str(HOME), "XDG_CONFIG_HOME": str(HOME / ".config"),
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false",
    "NO_PROXY": "*", "no_proxy": "*",
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
})
RESULTS, FAILURES = [], []


def check(name, ok, detail=""):
    RESULTS.append(name)
    if ok:
        print(f"ok   - {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL - {name}" + (f"  [{detail}]" if detail else ""))


def run_git(args, cwd=None, input=None):
    return subprocess.run(["git", *args], cwd=cwd, env=GITENV, input=input, text=True,
                          capture_output=True, timeout=60, check=False)


def write_helper(name, lines):
    p = HELPERS / name
    p.write_text("#!/bin/sh\n" + "\n".join(lines) + "\n")
    p.chmod(0o755)
    return str(p)


GET, SINK = '[ "${1:-get}" = get ] || exit 0', "cat >/dev/null"
# Reads the credential description and only answers for the target owner/*.git,
# so a later fetch really exercises the inherited useHttpPath setting.
READ_PATH = 'while IFS= read -r line; do case "$line" in path=*) path=${line#path=} ;; esac; done'
GOOD = write_helper("good.sh", [GET, "path=", READ_PATH,
                                'case "$path" in owner/*.git) ;; *) exit 0 ;; esac',
                                "printf 'username=%s\\npassword=%s\\n' \"$CC_USER\" \"$CC_PASS\""])
EMPTY_HELPER = write_helper("empty.sh", [GET, SINK])
WRONG_PATH = write_helper("wrongpath.sh", [GET, SINK,
    "printf 'username=%s\\npassword=%s\\npath=wrong/repo.git\\n' \"$CC_USER\" \"$CC_PASS\""])
WRONG_HOST = write_helper("wronghost.sh", [GET, SINK,
    "printf 'username=%s\\npassword=%s\\nhost=evil.example:9999\\n' \"$CC_USER\" \"$CC_PASS\""])
WRONG_PROTOCOL = write_helper("wrongprotocol.sh", [GET, SINK,
    "printf 'username=%s\\npassword=%s\\nprotocol=https\\n' \"$CC_USER\" \"$CC_PASS\""])
NO_SUFFIX = write_helper("no-suffix.sh", [GET, "path=", READ_PATH,
    'test "$path" = owner/no-suffix || exit 0',
    "printf 'username=%s\\npassword=%s\\n' \"$CC_USER\" \"$CC_PASS\""])
BAD_READER = write_helper("bad-reader.sh", [GET, SINK, "printf 'username=reader\\npassword=read-token\\n'"])
SECRETS = ["write-token", "read-token", base64.b64encode(b"writer:write-token").decode(),
           base64.b64encode(b"reader:read-token").decode()]
HELPER_CFG = lambda p: [["--add", "credential.helper", p]]
DEFAULT_CFG = HELPER_CFG(GOOD)
REPO = None


def make_bare(relpath, filename, content):
    seed = WORK / ("seed-" + relpath.replace("/", "_"))
    run_git(["init", "-q", "-b", "main", str(seed)])
    (seed / filename).write_text(content)
    run_git(["add", filename], cwd=seed)
    run_git(["commit", "-q", "-m", "add " + filename], cwd=seed)
    bare = WORK / "www" / relpath
    bare.parent.mkdir(parents=True, exist_ok=True)
    run_git(["clone", "-q", "--bare", str(seed), str(bare)])
    return seed, bare, run_git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()


def make_source(name, url, config):
    src = WORK / "src" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    run_git(["init", "-q", "-b", "main", str(src)])
    if url is not None:
        run_git(["remote", "add", "origin", url], cwd=src)
    for entry in config:
        run_git(["config", *entry], cwd=src)
    return src


# --- loopback smart-HTTP server: auth gate + git http-backend -----------------
CREDENTIALS = {("reader", "read-token"): "reader", ("writer", "write-token"): "writer"}
USER_SERVICES = {"reader": {"git-upload-pack"}, "writer": {"git-upload-pack", "git-receive-pack"}}


def _handler(server):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        log_message = log_error = lambda self, *a: None

        def do_GET(self):
            self._serve("GET")

        def do_POST(self):
            self._serve("POST")

        def _serve(self, method):
            parsed = urllib.parse.urlsplit(self.path)
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            body = self.rfile.read(length) if (length and method == "POST") else b""
            with server.lock:
                server.requests.append({"method": method, "path": parsed.path, "query": parsed.query,
                                        "authorization": self.headers.get("Authorization")})
            if server.mode == "redirect":
                return self._send(302, [("Location", f"http://127.0.0.1:{server.port}/elsewhere.git")], b"")
            if server.mode == "fake200":
                return self._send(200, [("Content-Type", "text/plain")], b"not a git service")
            user = self._authenticate()
            if user is None:
                return self._send(401, [("WWW-Authenticate", 'Basic realm="git"')], b"")
            if self._service(parsed.path, parsed.query) not in USER_SERVICES.get(user, ()):
                return self._send(403, [], b"forbidden")
            self._send(*self._backend(method, parsed.path, parsed.query, body, user))

        def _authenticate(self):
            header = self.headers.get("Authorization") or ""
            if not header.startswith("Basic "):
                return None
            try:
                user, _, password = base64.b64decode(header[6:]).decode().partition(":")
            except Exception:
                return None
            return CREDENTIALS.get((user, password))

        @staticmethod
        def _service(path, query):
            if path.endswith("/info/refs"):
                return urllib.parse.parse_qs(query).get("service", [""])[0]
            tail = path.rstrip("/").rsplit("/", 1)[-1]
            return tail if tail.startswith("git-") else ""

        def _backend(self, method, path, query, body, user):
            env = dict(GITENV)  # isolated: no inherited GIT_DIR/WORK_TREE/PARAMETERS
            env.update({
                "GIT_PROJECT_ROOT": server.root, "GIT_HTTP_EXPORT_ALL": "1",
                "PATH_INFO": path, "QUERY_STRING": query, "REQUEST_METHOD": method,
                "CONTENT_TYPE": self.headers.get("Content-Type", "") or "",
                "CONTENT_LENGTH": str(len(body)), "REMOTE_USER": user,
                "REMOTE_ADDR": "127.0.0.1", "SERVER_NAME": "127.0.0.1",
                "SERVER_PROTOCOL": "HTTP/1.1", "GATEWAY_INTERFACE": "CGI/1.1",
                "HTTP_GIT_PROTOCOL": self.headers.get("Git-Protocol", "") or ""})
            out = subprocess.run(["git", "http-backend"], input=body, capture_output=True, env=env).stdout
            cut, sep = out.find(b"\r\n\r\n"), 4
            if cut < 0:
                cut, sep = out.find(b"\n\n"), 2
            head, payload = (out, b"") if cut < 0 else (out[:cut], out[cut + sep:])
            status, headers = 200, []
            for line in head.split(b"\n"):
                line = line.rstrip(b"\r")
                if line:
                    name, _, value = line.partition(b":")
                    name, value = name.decode("latin1"), value.strip().decode("latin1")
                    if name.lower() == "status":
                        status = int(value.split()[0])
                    else:
                        headers.append((name, value))
            return status, headers, payload

        def _send(self, code, headers, body):
            self.send_response(code)
            for name, value in headers:
                self.send_header(name, value)
            if not any(k.lower() == "content-length" for k, _ in headers):
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

    return Handler


class GitServer:
    def __init__(self, root):
        self.root, self.mode, self.requests = str(root), "normal", []
        self.lock = threading.Lock()
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _handler(self))
        self.host = f"127.0.0.1:{self.httpd.server_address[1]}"
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def reset(self):
        with self.lock:
            self.requests = []

    def seen(self):
        with self.lock:
            return list(self.requests)

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def auth_user(request):
    header = request.get("authorization") or ""
    if not header.startswith("Basic "):
        return None
    try:
        return base64.b64decode(header[6:]).decode().split(":", 1)[0]
    except Exception:
        return None


class Result:
    def __init__(self, rc, out, err):
        self.returncode, self.stdout, self.stderr = rc, out, err


def run_target(args, creds=None, extra_env=None):
    env = dict(GITENV)
    env.update(extra_env or {})
    if creds:
        env["CC_USER"], env["CC_PASS"] = creds
    try:
        p = subprocess.run(["bash", str(TARGET), *args], capture_output=True, text=True,
                           env=env, timeout=180)
        return Result(p.returncode, p.stdout, p.stderr)
    except subprocess.TimeoutExpired as exc:
        return Result(124, exc.stdout or "", (exc.stderr or "") + "\n[timed out]")


def dest_config(dest):
    path = Path(dest) / ".git" / "config"
    return path.read_text() if path.is_file() else ""


def clone_case(server, name, creds, *, source_config=DEFAULT_CFG, extra_args=(), origin=None):
    url = origin or f"http://{server.host}/owner/repo.git"
    src = make_source("ok-" + name, url, source_config)
    dest = WORK / ("dest-" + name)
    server.reset()
    server.mode = "normal"
    return run_target(["--source", str(src), *extra_args, str(dest)], creds=creds), dest


def fail_case(server, name, *, source_config=DEFAULT_CFG, creds=("writer", "write-token"),
              extra_args=(), origin=None, mode="normal", http=None, keywords=(), requests_zero=False):
    url = origin or f"http://{server.host}/owner/repo.git"
    src = make_source("fail-" + name, url, source_config)
    dest = WORK / ("fail-dest-" + name)
    server.reset()
    server.mode = mode
    try:
        r = run_target(["--source", str(src), *extra_args, str(dest)], creds=creds)
    finally:
        server.mode = "normal"
    check(f"{name}: fails", r.returncode != 0)
    if http:
        check(f"{name}: HTTP {http}", f"HTTP {http}" in r.stderr, r.stderr.strip()[-160:])
    for word in keywords:
        check(f"{name}: message mentions {word}", word in r.stderr, r.stderr.strip()[-160:])
    if requests_zero:
        check(f"{name}: no request sent", len(server.seen()) == 0)
    check(f"{name}: no POST before failing", not any(x["method"] == "POST" for x in server.seen()))
    check(f"{name}: no destination created", not dest.exists())


# --- tests --------------------------------------------------------------------
def t_success(server):
    r, dest = clone_case(server, "basic", ("writer", "write-token"))
    check("success: exit 0", r.returncode == 0, r.stderr.strip()[-160:])
    f = dest / "hello.txt"
    check("success: HEAD and file cloned", run_git(["rev-parse", "HEAD"], cwd=dest).stdout.strip() == REPO[2]
          and f.is_file() and f.read_text() == "hello\n")
    rs = server.seen()
    check("success: receive-pack preflight then upload-pack POST",
          any(x["query"] == "service=git-receive-pack" for x in rs)
          and any(x["method"] == "POST" and x["path"].endswith("/git-upload-pack") for x in rs))
    check("success: never posts to the push service",
          not any(x["method"] == "POST" and x["path"].endswith("/git-receive-pack") for x in rs))
    check("success: reports the clone", "Cloned owner/repo" in r.stdout)
    check("success: plaintext and encoded secrets absent from stdout/stderr",
          all(secret not in r.stdout + r.stderr for secret in SECRETS))
    check("success: plaintext and encoded secrets absent from .git/config",
          all(secret not in dest_config(dest) for secret in SECRETS))


def t_read_only(server):
    r, dest = clone_case(server, "ro", ("reader", "read-token"), extra_args=("--read-only",))
    check("read-only: exit 0", r.returncode == 0, r.stderr.strip()[-160:])
    check("read-only: file cloned", (dest / "hello.txt").is_file())
    rs = server.seen()
    check("read-only: probes fetch, not push",
          any(x["query"] == "service=git-upload-pack" for x in rs)
          and not any(x["query"] == "service=git-receive-pack" for x in rs))
    check("read-only: says push unchecked", "read-only" in r.stdout.lower())


def t_read_token_denied_for_push(server):
    fail_case(server, "read-token-push", creds=("reader", "read-token"), http=403,
              keywords=("--repo", "--helper", "--read-only"))


def t_wrong_token(server):
    fail_case(server, "wrong-token", creds=("reader", "bogus-token"), http=401)


def t_missing_and_empty_credentials(server):
    fail_case(server, "no-creds", source_config=[], creds=None, keywords=("--helper",), requests_zero=True)
    fail_case(server, "empty-helper", source_config=HELPER_CFG(EMPTY_HELPER),
              keywords=("--helper",), requests_zero=True)


def t_helper_bad_identity(server):
    fail_case(server, "wrong-path", source_config=HELPER_CFG(WRONG_PATH),
              keywords=("--repo",), requests_zero=True)
    fail_case(server, "wrong-host", source_config=HELPER_CFG(WRONG_HOST),
              keywords=("--repo",), requests_zero=True)
    fail_case(server, "wrong-protocol", source_config=HELPER_CFG(WRONG_PROTOCOL),
              keywords=("--repo",), requests_zero=True)


def t_bad_origin(server):
    fail_case(server, "ssh-origin", origin="ssh://git@127.0.0.1/owner/repo.git",
              keywords=("HTTPS",), requests_zero=True)
    fail_case(server, "embedded-creds", origin=f"http://user:pass@{server.host}/owner/repo.git",
              keywords=("HTTPS",), requests_zero=True)


def t_redirect_and_fake_ad(server):
    fail_case(server, "redirect", mode="redirect", keywords=("redirect",))
    check("redirect: never followed", len(server.seen()) == 1
          and server.seen()[0]["path"] == "/owner/repo.git/info/refs")
    fail_case(server, "fake-200", mode="fake200", keywords=("advertise",))


def t_existing_destination(server):
    src = make_source("existing", f"http://{server.host}/owner/repo.git", DEFAULT_CFG)
    dest = WORK / "dest-existing"
    dest.mkdir()
    sentinel = dest / "keep.txt"
    sentinel.write_text("untouched\n")
    server.reset()
    r = run_target(["--source", str(src), str(dest)], creds=("writer", "write-token"))
    check("existing destination: fails", r.returncode != 0)
    check("existing destination: message", "exists" in r.stderr, r.stderr.strip()[-160:])
    check("existing destination: left untouched", sentinel.read_text() == "untouched\n")
    check("existing destination: no request sent", len(server.seen()) == 0)


def t_explicit_helper(server):
    # Source-local helper returns reader creds; an explicit --helper must win.
    r, _ = clone_case(server, "explicit", ("writer", "write-token"),
                      source_config=HELPER_CFG(BAD_READER), extra_args=("--helper", GOOD))
    check("explicit helper: exit 0", r.returncode == 0, r.stderr.strip()[-160:])
    check("explicit helper: write creds used over source-local helper",
          any(auth_user(x) == "writer" for x in server.seen()),
          str([auth_user(x) for x in server.seen()]))


def t_multi_helper_reset_scoped(server):
    url = f"http://{server.host}/owner/repo.git"
    src = make_source("multi", url, [
        ["--add", "credential.helper", BAD_READER],      # reader creds
        ["--add", "credential.helper", ""],              # empty value resets the list
        ["--add", f"credential.{url}.helper", GOOD]])    # URL-scoped: writer creds
    dest = WORK / "dest-multi"
    server.reset()
    r = run_target(["--source", str(src), str(dest)], creds=("writer", "write-token"))
    check("multi-helper: exit 0 (order+reset+scope honoured)", r.returncode == 0, r.stderr.strip()[-160:])
    rs = server.seen()
    check("multi-helper: scoped writer used, earlier helper reset away",
          any(auth_user(x) == "writer" for x in rs) and not any(auth_user(x) == "reader" for x in rs))
    check("multi-helper: secret absent from .git/config",
          "read-token" not in dest_config(dest) and "write-token" not in dest_config(dest))


def t_global_and_bare_credentials(server):
    for bare in (False, True):
        label = "global-bare" if bare else "global-helper"
        src = make_source(label, f"http://{server.host}/owner/repo.git", [])
        config = WORK / f"{label}.gitconfig"
        run_git(["config", "--file", str(config), "credential.helper", GOOD])
        if bare:
            with config.open("a") as stream:
                stream.write("[credential]\n\tuseHttpPath\n\tinteractive\n")
        dest = WORK / f"dest-{label}"
        server.reset()
        r = run_target(["--source", str(src), str(dest)], ("writer", "write-token"),
                       {"GIT_CONFIG_GLOBAL": str(config)})
        check(f"{label}: clone succeeds", r.returncode == 0, r.stderr.strip()[-160:])
        check(f"{label}: real content cloned", (dest / "hello.txt").is_file())


def t_suffixless_origin(server):
    _, _, head = make_bare("owner/no-suffix", "suffixless.txt", "without suffix\n")
    url = f"http://{server.host}/owner/no-suffix"
    r, dest = clone_case(server, "suffixless", ("writer", "write-token"), origin=url,
                         source_config=[["--add", f"credential.{url}.helper", NO_SUFFIX]])
    check("suffixless: scoped helper still matches", r.returncode == 0, r.stderr.strip()[-160:])
    check("suffixless: correct repository cloned", (dest / "suffixless.txt").is_file()
          and run_git(["rev-parse", "HEAD"], cwd=dest).stdout.strip() == head)


def t_repo_override(server):
    make_bare("owner/other.git", "other.txt", "other\n")
    r, dest = clone_case(server, "repo-other", ("writer", "write-token"),
                         extra_args=("--repo", "owner/other"))
    check("repo override: exit 0", r.returncode == 0, r.stderr.strip()[-160:])
    check("repo override: cloned owner/other",
          (dest / "other.txt").is_file() and not (dest / "hello.txt").exists())
    fail_case(server, "repo-ghost", extra_args=("--repo", "owner/ghost"), http=404)


def t_inherited_fetch(server):
    # The good helper only answers for owner/*.git paths, so a working fetch
    # proves the clone inherited both the helper and useHttpPath.
    seed, bare, _ = make_bare("owner/fetch.git", "data.txt", "v1\n")
    r, dest = clone_case(server, "fetch", ("writer", "write-token"),
                         origin=f"http://{server.host}/owner/fetch.git")
    check("fetch: initial clone ok", r.returncode == 0, r.stderr.strip()[-160:])
    if not dest.is_dir():
        return check("fetch: clone missing, cannot continue", False)
    (seed / "data.txt").write_text("v2\n")
    run_git(["add", "data.txt"], cwd=seed)
    run_git(["commit", "-q", "-m", "v2"], cwd=seed)
    newhead = run_git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
    run_git(["push", "-q", str(bare), "main"], cwd=seed)
    env = dict(GITENV)
    env.update({"CC_USER": "writer", "CC_PASS": "write-token"})
    server.reset()
    f = subprocess.run(["git", "-C", str(dest), "fetch", "origin"], env=env, text=True,
                       capture_output=True, timeout=60, check=False)
    check("fetch: later fetch inherits helper+useHttpPath", f.returncode == 0, f.stderr.strip()[-160:])
    check("fetch: new upstream commit fetched",
          run_git(["rev-parse", "origin/main"], cwd=dest).stdout.strip() == newhead)


def main():
    if run_git(["--version"]).returncode:
        sys.exit("error: git is required")
    global REPO
    REPO = make_bare("owner/repo.git", "hello.txt", "hello\n")
    server = GitServer(WORK / "www")
    print(f"# target : {TARGET}\n# server : http://{server.host}\n# work   : {WORK}")
    try:
        for test in [t_success, t_read_only, t_read_token_denied_for_push, t_wrong_token,
                     t_missing_and_empty_credentials, t_helper_bad_identity, t_bad_origin,
                     t_redirect_and_fake_ad, t_existing_destination, t_explicit_helper,
                     t_multi_helper_reset_scoped, t_global_and_bare_credentials,
                     t_suffixless_origin, t_repo_override, t_inherited_fetch]:
            try:
                test(server)
            except Exception as exc:
                check(f"{test.__name__}: raised", False, repr(exc))
    finally:
        server.stop()
    failed = len(FAILURES)
    print(f"\n# {len(RESULTS) - failed}/{len(RESULTS)} checks passed")
    if failed:
        print("# FAILURES: " + ", ".join(FAILURES) + f"\n# work dir kept: {WORK}")
        return 1
    shutil.rmtree(WORK, ignore_errors=True)
    print("# all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
