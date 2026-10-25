#!/usr/bin/env python3
"""Mount a read-only view through a built libfuse, the way a room's executor does.

`fusepy` loads the library by path (`FUSE_LIBRARY_PATH`, which `bootstrap.
transcript_view_env` sets) rather than through the loader's own search, because
the machine's `ldconfig` is root's to change and the platform does not ask for
root on it. So the thing worth checking is exactly that: a library handed to it
by path mounts, serves bytes, and refuses a write.

    python3 .github/scripts/fuse-view-smoke.py <path to libfuse.so.2>

Run by `.github/workflows/fuse-runtime.yml`; needs the distribution's own
`fusermount` on the machine it runs on and `fusepy` importable by the
interpreter running this.

Not mounted `allow_other`, unlike the executor's own view (`transcript_view`):
the file is read back by the very process that mounted it, and `allow_other`
is refused unless `/etc/fuse.conf` carries `user_allow_other` — which the
executor writes as root and a CI runner does not, so asking for it would make
this fail on a perfectly good library.
"""

import os
import subprocess
import sys
import tempfile
import textwrap
import time

VIEW = "transcript.jsonl"
BODY = '{"type":"assistant"}\n'


def fusepy_directory():
    """Where the `fuse.py` a room's executor ships is, in this checkout.

    `release.sources` reads it out of the installed distribution, so that is
    where the smoke test takes it from too: a test that used a different copy
    would prove the wrong file loads. Not finding it is a failure and not an
    empty path — `sys.path.insert(0, "")` is the process's own directory, so
    the server would quietly load whatever `fuse.py` sat in the cwd."""
    from importlib.metadata import distribution

    located = distribution("fusepy").locate_file("fuse.py")
    directory = os.path.dirname(str(located))
    if not os.path.isfile(located):
        raise SystemExit(f"fusepy is not where it says it is: {located}")
    return directory


def main(library):
    fusepy_dir = fusepy_directory()
    work = tempfile.mkdtemp()
    view = os.path.join(work, "view")
    source = os.path.join(work, "src")
    os.mkdir(view)
    with open(source, "w") as handle:
        handle.write(BODY)

    script = os.path.join(work, "serve.py")
    with open(script, "w") as handle:
        handle.write(
            textwrap.dedent(
                f"""
                import os, sys
                # `fusepy` is what a room's executor ships beside its own code
                # (`release.sources`); on the runner it comes from the working
                # copy, so the smoke test loads the same file the machine does.
                sys.path.insert(0, {fusepy_dir!r})
                os.environ["FUSE_LIBRARY_PATH"] = {library!r}
                from fuse import FUSE, Operations

                class Ops(Operations):
                    def __init__(self, path):
                        self.path = path

                    def getattr(self, path, fh=None):
                        st = os.lstat(self.path)
                        return {{"st_mode": st.st_mode, "st_nlink": 1,
                                "st_size": st.st_size, "st_mtime": 0,
                                "st_ctime": 0, "st_atime": 0}}

                    def readdir(self, path, fh=None):
                        return [".", "..", {VIEW!r}]

                    def read(self, path, size, offset, fh=None):
                        with open(self.path, "rb") as source:
                            source.seek(offset)
                            return source.read(size)

                    def open(self, path, flags):
                        return 0

                FUSE(Ops({source!r}), {view!r}, foreground=True, ro=True,
                     direct_io=True)
                """
            )
        )

    log = os.path.join(work, "serve.log")
    with open(log, "w") as errors:
        server = subprocess.Popen(
            [sys.executable, script], stdout=errors, stderr=subprocess.STDOUT
        )
        try:
            # Read the file rather than ask `os.path.ismount`: the same reason
            # the executor does (`transcript_view._mounted`). What is being
            # checked is that the bytes come through, and a mount that is up
            # but not served is not up.
            got = None
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise SystemExit(
                        f"the view's server exited with {server.returncode}:\n"
                        + open(log).read()
                    )
                try:
                    with open(os.path.join(view, VIEW)) as handle:
                        got = handle.read()
                    break
                except OSError:
                    time.sleep(0.25)
            if got is None:
                raise SystemExit("the view did not come up:\n" + open(log).read())
            if got != BODY:
                raise SystemExit(f"read back {got!r}, not {BODY!r}")
            try:
                with open(os.path.join(view, VIEW), "w") as handle:
                    handle.write("x")
            except OSError as error:
                if error.errno != 30:  # EROFS
                    raise
            else:
                raise SystemExit("a write to the view was not refused")
            print("mounted through the placed library, read, and refused a write")
        finally:
            subprocess.run(["fusermount", "-uz", view], check=False)
            server.kill()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
