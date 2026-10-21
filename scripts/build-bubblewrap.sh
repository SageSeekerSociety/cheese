#!/bin/sh
# Build the static bwrap cheesehost places on a Linux machine, from the pinned
# upstream release. Upstream publishes source only, and the copy a machine's
# system may have is not one the platform can rely on, so the platform builds
# its own. Runs in the Alpine image `.github/workflows/bubblewrap.yml` names:
# musl links statically, so one binary serves every Linux distribution.
#
#   sh scripts/build-bubblewrap.sh <output dir>
set -eu
version=0.13.0
revision=0.13.0-cheese.2
source_sha256=4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765
# bwrap links musl and libcap statically from Alpine's packages, which carry
# no license texts: each is taken from upstream at the version Alpine built,
# and the build stops when Alpine has moved on.
musl_version=1.2.5
musl_copyright_sha256=f9bc4423732350eb0b3f7ed7e91d530298476f8fec0c6c427a1c04ade22655af
libcap_version=2.78
libcap_license_sha256=68467e731f4744bd6e0bb69e8df9c3a994e09cd6b203d0c41327ac6d079c581d
out=$(mkdir -p "${1:?output directory}" && cd "$1" && pwd)

case "$(uname -m)" in
  x86_64) platform=linux-x64 ;;
  aarch64) platform=linux-arm64 ;;
  *) echo "no build for $(uname -m)" >&2; exit 1 ;;
esac

apk add --no-cache build-base meson libcap-dev libcap-static curl xz

work=$(mktemp -d)
cd "$work"
curl -fsSL -o source.tar.xz \
  "https://github.com/containers/bubblewrap/releases/download/v$version/bubblewrap-$version.tar.xz"
echo "$source_sha256  source.tar.xz" | sha256sum -c -
tar -xf source.tar.xz
cd "bubblewrap-$version"
meson setup build --buildtype=release --prefer-static -Ddefault_library=static \
  -Dc_link_args=-static -Dselinux=disabled -Dman=disabled -Dtests=false \
  -Dbash_completion=disabled -Dzsh_completion=disabled
meson compile -C build
strip build/bwrap
# A machine gets this one file: it must load no shared library.
if readelf -d build/bwrap | grep -q NEEDED; then
  echo "bwrap is not static" >&2
  exit 1
fi
build/bwrap --version

package="bubblewrap-$revision"
mkdir -p "$package/licenses"
cp build/bwrap "$package/"
cp COPYING "$package/licenses/bubblewrap-$version-COPYING"
for linked in "musl-dev $musl_version" "libcap-static $libcap_version"; do
  set -- $linked
  have=$(apk list -I "$1" | sed -n "s/^$1-\(.*\)-r[0-9]* .*/\1/p")
  if [ "$have" != "$2" ]; then
    echo "Alpine's $1 is ${have:-not installed}; the recipe's license text is for $2" >&2
    exit 1
  fi
done
curl -fsSL --retry 5 --retry-all-errors -o "$package/licenses/musl-$musl_version-COPYRIGHT" \
  "https://git.musl-libc.org/cgit/musl/plain/COPYRIGHT?h=v$musl_version"
echo "$musl_copyright_sha256  $package/licenses/musl-$musl_version-COPYRIGHT" | sha256sum -c -
curl -fsSL --retry 5 --retry-all-errors -o "$package/licenses/libcap-$libcap_version-License" \
  "https://git.kernel.org/pub/scm/libs/libcap/libcap.git/plain/License?h=libcap-$libcap_version"
echo "$libcap_license_sha256  $package/licenses/libcap-$libcap_version-License" | sha256sum -c -
{
  printf '%s\n%s\n\n' "bubblewrap $version (bwrap), linked statically from these components, built by" \
    "scripts/build-bubblewrap.sh. Each component's license texts are under licenses/."
  printf '%-14s %-15s %s\n' component version license \
    bubblewrap "$version" LGPL-2.0-or-later \
    libcap "$libcap_version" "BSD-3-Clause OR GPL-2.0-only" \
    musl "$musl_version" MIT
} > "$package/NOTICE"
archive="$out/$package-$platform.tar.gz"
tar -czf "$archive" "$package"
(cd "$out" && sha256sum "$(basename "$archive")" > "$(basename "$archive").sha256")
cat "$archive.sha256"
