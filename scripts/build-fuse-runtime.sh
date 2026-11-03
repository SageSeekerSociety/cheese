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
# `-cheese.2`: the header patch below, without which there is no arm64 artifact
# at all. x64's bytes are unchanged by it — verified by building both ways and
# comparing — but one release name covers both architectures, and the first
# published arm64 library is a different artifact from anything the name would
# otherwise have stood for.
revision="2.9.9-cheese.2"
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
# Where the checkout is, for the patch this applies (the same spelling
# `build-forge-cli.sh` uses). The build itself runs in a temporary directory and
# never needs it.
root=$(cd "$(dirname "$0")/.." && pwd)
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

# The toolchain, from the baseline distribution's own packages. Unlike
# `build-bubblewrap.sh` and `build-host-tools.sh`, whose image is Alpine and
# whose line is `apk add`, the base here is a bare Debian image and has no
# compiler, `make`, `binutils` or even `curl` — so the recipe installs what it
# runs, rather than leaving it to the caller to have done.
#
# The toolchain is not pinned, and it is a build input like any other: a
# bookworm point release that bumps `gcc-12` or `binutils` changes the code
# this produces, so a digest pinned from one run is reproduced only by a
# machine carrying the same versions. What is verified today is the weaker
# statement — two builds of one commit on one machine, at one moment, are
# byte-identical — which is what makes the digest worth recording, since
# without the flags below even that pair differs. The bubblewrap and host-tools
# recipes take the same position: only the image is pinned, not `apk`'s
# packages. Pinning the versions here would mean naming a snapshot archive, and
# that belongs with the release step that first pins a digest.
#
# The network options are `.github/scripts/ensure-apt.sh`'s, carried inline:
# that helper runs `sudo apt-get "$@"` and cannot be used from inside this
# image, but the reason it has them applies here too — apt's own network waits
# are unbounded, and a stalled mirror otherwise holds the job to its own
# timeout with nothing in the log. `Error-Mode=any` makes `update` report a
# failed index rather than exiting 0 on one, so a mirror that answered nothing
# usable fails here instead of at a `configure` run that cannot say why. This
# script is `sh`, so the flags are a split string rather than the helper's
# array.
apt_net="-o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 -o Acquire::Retries=3 -o APT::Update::Error-Mode=any"
export DEBIAN_FRONTEND=noninteractive
# shellcheck disable=SC2086 # separate -o flags: the split is the point
apt-get $apt_net update -qq
# shellcheck disable=SC2086
apt-get $apt_net install -y -qq build-essential curl ca-certificates >/dev/null

work=$(mktemp -d)
cd "$work"
curl -fsSL --retry 5 --retry-all-errors -o source.tar.gz "$source_url"
echo "$source_sha256  source.tar.gz" | sha256sum -c -
tar -xf source.tar.gz
cd "fuse-$version"
# The one header patch this needs, applied the way `build-forge-cli.sh` applies
# its own: `-R --dry-run` first, so a tree that already carries it is left
# alone. Upstream's `fuse_kernel.h` opens by defining five type *macros*
# (`__u64 uint64_t` and the rest) and never undefines them, so the names stay
# rewritten for the whole translation unit that includes it. On aarch64 that
# breaks the build: `<signal.h>` reaches `<asm-generic/int-ll64.h>`, whose
# `typedef unsigned long long __u64;` becomes a second `uint64_t` with a
# different underlying type, and `fuse_loop_mt.c` — which includes
# `fuse_lowlevel.h` before `fuse_kernel.h` — stops with "conflicting types for
# uint64_t". x86_64 never reached that header this way, which is why only the
# arm leg failed. Nothing needs the macros past the last struct, so the patch
# takes them back out at the end of the file. The patch is the whole change:
# the recipe stays otherwise stock, so a digest still names upstream's source.
#
# `patch` comes from the baseline image's `dpkg-dev`, which `build-essential`
# depends on. Checked rather than assumed: without it the `||` branch would
# also fail with "command not found" under `set -eu`, which is loud, but the
# message would be about a missing command rather than about why it is needed.
command -v patch >/dev/null || {
  echo "no patch(1); the header fix cannot be applied and aarch64 will not build" >&2
  exit 1
}
patchfile="$root/scripts/patches/fuse-kernel-undef-types.patch"
[ -f "$patchfile" ] || { echo "no $patchfile" >&2; exit 1; }
patch --force -R -p1 --dry-run < "$patchfile" >/dev/null 2>&1 \
  || patch --force -p1 < "$patchfile"
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
