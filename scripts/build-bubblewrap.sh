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
revision=0.13.0-cheese.1
source_sha256=4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765
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
mkdir "$package"
cp build/bwrap COPYING "$package/"
archive="$out/$package-$platform.tar.gz"
tar -czf "$archive" "$package"
(cd "$out" && sha256sum "$(basename "$archive")" > "$(basename "$archive").sha256")
cat "$archive.sha256"
