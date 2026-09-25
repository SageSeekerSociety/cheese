#!/bin/sh
# Builds a tmux for macOS that needs nothing outside the base system, so the
# desktop app can hand it to a Mac that has no Homebrew. cheesehost will not
# start without tmux and looks for a private copy before the one on PATH.
#
# libevent and utf8proc are linked statically; ncurses is the system's own.
#
#   build-tmux.sh <arm64|x86_64> <output-file>
set -eu

ARCH="$1"
mkdir -p "$(dirname "$2")"
OUT="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"

TMUX_VERSION=3.5a
LIBEVENT_VERSION=2.1.12-stable
UTF8PROC_VERSION=2.9.0

WORK="$(mktemp -d "${TMPDIR:-/tmp}/tmux-build.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
PREFIX="$WORK/prefix"
export CFLAGS="-arch $ARCH -mmacosx-version-min=11.0 -O2"
export LDFLAGS="-arch $ARCH -mmacosx-version-min=11.0"
HOST="$([ "$ARCH" = arm64 ] && echo aarch64-apple-darwin || echo x86_64-apple-darwin)"

cd "$WORK"
curl -fsSL "https://github.com/libevent/libevent/releases/download/release-$LIBEVENT_VERSION/libevent-$LIBEVENT_VERSION.tar.gz" | tar xz
curl -fsSL "https://github.com/JuliaStrings/utf8proc/archive/refs/tags/v$UTF8PROC_VERSION.tar.gz" | tar xz
curl -fsSL "https://github.com/tmux/tmux/releases/download/$TMUX_VERSION/tmux-$TMUX_VERSION.tar.gz" | tar xz

(cd "libevent-$LIBEVENT_VERSION" &&
  ./configure --host="$HOST" --prefix="$PREFIX" --disable-shared --enable-static \
    --disable-openssl --disable-samples --disable-libevent-regress >/dev/null &&
  make -j4 >/dev/null && make install >/dev/null)

(cd "utf8proc-$UTF8PROC_VERSION" &&
  make CFLAGS="$CFLAGS" libutf8proc.a >/dev/null &&
  mkdir -p "$PREFIX/lib" "$PREFIX/include" &&
  cp libutf8proc.a "$PREFIX/lib/" && cp utf8proc.h "$PREFIX/include/")

(cd "tmux-$TMUX_VERSION" &&
  LIBEVENT_CFLAGS="-I$PREFIX/include" LIBEVENT_LIBS="$PREFIX/lib/libevent_core.a" \
  LIBUTF8PROC_CFLAGS="-I$PREFIX/include" LIBUTF8PROC_LIBS="$PREFIX/lib/libutf8proc.a" \
  CPPFLAGS="-I$PREFIX/include" \
  ./configure --host="$HOST" --enable-utf8proc >/dev/null &&
  make -j4 >/dev/null)

cp "tmux-$TMUX_VERSION/tmux" "$OUT"
strip "$OUT"
# Anything but /usr/lib and /System here means a Mac without Homebrew cannot run it.
if otool -L "$OUT" | tail -n +2 | grep -v -E '^\s+(/usr/lib/|/System/)'; then
  echo "tmux links a library outside the base system" >&2
  exit 1
fi
echo "built $OUT ($ARCH)"
