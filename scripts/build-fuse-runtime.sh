#!/bin/sh
# Build the libfuse 2 runtime cheesehost places on a Linux machine, from the
# pinned upstream release. Runs in the image `.github/workflows/fuse-runtime.yml`
# names, which is a glibc distribution and not Alpine: the library is loaded
# with `dlopen` by the `fusepy` a room's executor ships, so it has to be ABI
# compatible with the machine that loads it — and a musl build links against
# `/lib/ld-musl-*.so.1`, which a glibc machine's loader cannot resolve. It was
# built that way first and would not load at all, so the rule here is the
# opposite of `build-bubblewrap.sh`'s: as old a glibc as the source builds
# against, so one artifact serves the newer machines too. Debian 12 (glibc
# 2.34) is where that was verified; it covers Ubuntu 22.04 and RHEL 9 and up.
#
#   sh scripts/build-fuse-runtime.sh <output dir>
set -eu
version=2.9.9
revision="2.9.9-cheese.1"
# The release tarball's own text, from libfuse's `COPYING` and `COPYING.LIB`.
# FUSE 2.9.9 is LGPL-2.1 for the library and GPL-2.0 for the utilities; the
# utilities are not built here (`--disable-util`), so the package is the
# library's license.
source_url="https://github.com/libfuse/libfuse/releases/download/fuse-$version/fuse-$version.tar.gz"
source_sha256=d0e69d5d608cc22ff4843791ad097f554dd32540ddc9bed7638cc6fea7c1b4b5
# The baseline distribution, and the whole reason this build is not the static
# musl one `build-bubblewrap.sh` makes. glibc symbols are versioned and a newer
# one does not run on an older machine, so the build has to happen *inside* this
# image and not on the workflow's runner. Checked below rather than trusted: a
# step that quietly stopped using the image would still produce a library that
# works on the runner and loads on nothing older.
baseline=debian:bookworm
baseline_codename=bookworm

out=$(mkdir -p "${1:?output directory}" && cd "$1" && pwd)
case "$(uname -m)" in
  x86_64) platform=linux-x64 ;;
  aarch64) platform=linux-arm64 ;;
  *) echo "no build for $(uname -m)" >&2; exit 1 ;;
esac

if [ "$(sed -n 's/^VERSION_CODENAME=//p' /etc/os-release)" != "$baseline_codename" ]; then
  echo "this has to build inside $baseline: glibc symbols are versioned, and a" >&2
  echo "library built on a newer one refuses to load on an older machine." >&2
  exit 1
fi

work=$(mktemp -d)
cd "$work"
curl -fsSL --retry 5 --retry-all-errors -o source.tar.gz "$source_url"
echo "$source_sha256  source.tar.gz" | sha256sum -c -
tar -xf source.tar.gz
cd "fuse-$version"
# `--disable-util` leaves out `fusermount`, deliberately. The library falls
# back to `execvp("fusermount")` (`lib/mount.c`) when the path it was compiled
# with is not there, so a machine's own distribution copy — the one with the
# setuid bit, and the one the kernel's mount permissions are set up for — is
# used, and this package holds no setuid binary at all.
#
# The three flags make two builds of one commit byte-identical, so the digest
# in `agent/toolchain.py` names the recipe and not this run's temp directory:
# `-ffile-prefix-map` takes the checkout path out of the objects,
# `--build-id=none` takes the hash of the whole build out of the ELF, and the
# `-g0` keeps the debug info those paths were in. Without them the archive
# differed by 29 bytes between two runs of the same commit, which makes a
# pinned digest unverifiable.
CFLAGS="-g0 -O2 -ffile-prefix-map=$work/fuse-$version=." \
  LDFLAGS="-Wl,--build-id=none" \
  ./configure --prefix=/usr --disable-example --disable-util --disable-nls
make -j"$(nproc)"
strip --strip-debug "lib/.libs/libfuse.so.$version"
# A machine gets this one file, at the absolute path `/etc/ld.so.conf`-free
# linkers do not need: the executor points `fusepy` at it (`FUSE_LIBRARY_PATH`).
library="lib/.libs/libfuse.so.$version"
if ! readelf -d "$library" | grep -q 'SONAME.*libfuse.so.2'; then
  echo "the library does not carry fusepy's soname" >&2
  exit 1
fi
if readelf -d "$library" | grep NEEDED | grep -q musl; then
  echo "$library links musl; a glibc machine's loader cannot load it" >&2
  readelf -d "$library" | grep NEEDED >&2
  exit 1
fi
# The newest glibc symbol this needs, read back rather than assumed: a build
# that quietly moved to the runner's own glibc would otherwise ship a library
# that refuses to load on every older one, with a message about a version
# nobody can act on.
needed=$(objdump -T "$library" | sed -n 's/.*GLIBC_\([0-9][0-9.]*\).*/\1/p' | sort -uV | tail -1)
echo "needs glibc $needed, built on $baseline"

package="fuse-$revision"
mkdir -p "$package/licenses"
cp "$library" "$package/libfuse.so.2"
cp COPYING.LIB "$package/licenses/libfuse-$version-COPYING.LIB"
{
  printf '%s\n\n' \
    "libfuse $version, the FUSE 2 userspace library, built by" \
    "scripts/build-fuse-runtime.sh on $baseline (glibc $needed). Its license" \
    "text is under licenses/."
  printf '%-14s %-15s %s\n' component version license libfuse "$version" LGPL-2.1-only
} > "$package/NOTICE"
archive="$out/$package-$platform.tar.gz"
# Every timestamp in the archive is the source's own, and the order is the
# same each time, so the digest in `agent/toolchain.py` names this recipe
# rather than the day it ran.
tar --sort=name --owner=0 --group=0 --numeric-owner --mtime=@0 \
  -czf "$archive" "$package"
(cd "$out" && sha256sum "$(basename "$archive")" > "$(basename "$archive").sha256")
cat "$archive.sha256"
