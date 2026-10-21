#!/bin/sh
# Build the static programs cheesehost places on a Linux machine besides bwrap
# (`scripts/build-bubblewrap.sh`), from pinned upstream releases. Runs in the
# Alpine image `.github/workflows/host-tools.yml` names: musl links statically,
# so one binary serves every Linux distribution, and the machine needs no
# package of its own for any of it.
#
#   host-tools  what a platform host's sandboxes are made with as root
#               (`remote_execution/sandbox_host.py`) and its data disk is
#               formatted with (`machine/enrollment.py`): ip, iptables (the
#               nf_tables backend every current distribution uses), mkfs.xfs and
#               xfs_quota. Placed on every Linux machine.
#   tmux, git   what the connector and its rooms need where the system's own is
#               missing or too old (`cli/internal/devenv`).
#
#   sh scripts/build-host-tools.sh <output dir>
set -eu
revision=1
iproute2_version=7.2.0
iproute2_sha256=4c2fa124c2cf0afd7ca34d1eeacba6ba048a56f6374e2aab93dafbdbd4eea9c0
iptables_version=1.8.13
iptables_sha256=1afcd33da9e8f913ace6a2126788162e207e26f5d5e29c6573c0e581ffc58b99
libnftnl_version=1.3.2
libnftnl_sha256=c97abc3409f8fa396b4462b2bb7f147a3a47a4ddc97cfa0b2f18890c9cfde8b0
xfsprogs_version=7.2.0
xfsprogs_sha256=501dfa363cd8e19997a4a1a71f70ded88241b45e9db233e8875c23b80520cc83
inih_version=r62
inih_sha256=9c15fa751bb8093d042dae1b9f125eb45198c32c6704cd5481ccde460d4f8151
tmux_version=3.5a
tmux_sha256=16216bd0877170dfcc64157085ba9013610b12b082548c7c9542cc0103198951
curl_version=8.22.0
curl_sha256=f7ef3ae8a22e521f289803fe93543eb64c329b58aa73a9e224dfd915a2a5f4f7
git_version=2.56.0
git_sha256=26c56c296b38c0695b26fa95f475f1d01704d2d38e73465ca30b0b2f5dc789d3
out=$(mkdir -p "${1:?output directory}" && cd "$1" && pwd)

case "$(uname -m)" in
  x86_64) platform=linux-x64 ;;
  aarch64) platform=linux-arm64 ;;
  *) echo "no build for $(uname -m)" >&2; exit 1 ;;
esac

apk add --no-cache build-base pkgconf autoconf automake libtool meson bison flex \
  linux-headers bash perl curl xz gettext-dev \
  libmnl-dev libmnl-static \
  userspace-rcu-dev userspace-rcu-static util-linux-dev util-linux-static \
  libevent-dev libevent-static ncurses-dev ncurses-static \
  openssl-dev openssl-libs-static zlib-dev zlib-static expat-dev expat-static

work=$(mktemp -d)
prefix="$work/prefix"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig"
cd "$work"

fetch() { # url sha256
  curl -fsSL --retry 5 --retry-all-errors -o "$(basename "$1")" "$1"
  echo "$2  $(basename "$1")" | sha256sum -c -
  tar -xf "$(basename "$1")"
}

# Every program shipped must load no shared library: a machine gets the file.
static() {
  for program in "$@"; do
    if ! readelf -h "$program" >/dev/null || readelf -d "$program" | grep -q NEEDED; then
      echo "$program is not static" >&2
      exit 1
    fi
  done
}

# --- iproute2: ip ---------------------------------------------------------
fetch "https://mirrors.edge.kernel.org/pub/linux/utils/net/iproute2/iproute2-$iproute2_version.tar.xz" "$iproute2_sha256"
(
  cd "iproute2-$iproute2_version"
  ./configure --libbpf_force off
  # SHARED_LIBS=n: a link kind (veth) is found in a table compiled in, where
  # a static binary cannot look itself up with dlopen.
  make -j"$(nproc)" SUBDIRS="lib ip" SHARED_LIBS=n LDFLAGS=-static
)

# --- iptables (nf_tables backend), and the libnftnl it needs --------------
fetch "https://www.netfilter.org/pub/libnftnl/libnftnl-$libnftnl_version.tar.xz" "$libnftnl_sha256"
(
  cd "libnftnl-$libnftnl_version"
  ./configure --prefix="$prefix" --enable-static --disable-shared
  make -j"$(nproc)" install
)
fetch "https://www.netfilter.org/pub/iptables/iptables-$iptables_version.tar.xz" "$iptables_sha256"
(
  cd "iptables-$iptables_version"
  # Static: every match and target is linked in, so none is looked up as a
  # shared object on the machine.
  ./configure --enable-static --disable-shared \
    --disable-libipq --disable-bpf-compiler --disable-nfsynproxy --disable-connlabel
  make -j"$(nproc)" LDFLAGS=-all-static
)

# --- xfsprogs: mkfs.xfs, xfs_quota, and the inih mkfs reads its config with
fetch "https://github.com/benhoyt/inih/archive/refs/tags/$inih_version.tar.gz" "$inih_sha256"
(
  cd "inih-$inih_version"
  meson setup build --prefix="$prefix" --libdir=lib --default-library=static \
    -Ddistro_install=true -Dwith_INIReader=false
  meson install -C build
)
fetch "https://mirrors.edge.kernel.org/pub/linux/utils/fs/xfs/xfsprogs/xfsprogs-$xfsprogs_version.tar.xz" "$xfsprogs_sha256"
(
  cd "xfsprogs-$xfsprogs_version"
  # Flags go in at configure: xfsprogs' own make variables carry what it
  # detected, and one given to make replaces them.
  CFLAGS="-O2 -I$prefix/include" LDFLAGS="-L$prefix/lib" \
    ./configure --enable-static --disable-shared --enable-gettext=no \
    --enable-editline=no --enable-scrub=no --enable-lib64=no
  make -j"$(nproc)" LOADERFLAGS=-all-static
)

# --- tmux ------------------------------------------------------------------
fetch "https://github.com/tmux/tmux/releases/download/$tmux_version/tmux-$tmux_version.tar.gz" "$tmux_sha256"
(
  cd "tmux-$tmux_version"
  ./configure --enable-static
  make -j"$(nproc)"
)

# --- git, with https through a libcurl of its own --------------------------
# Only what git's https needs: TLS through OpenSSL, which finds the machine's
# CA certificates where its distribution keeps them (its default paths), and
# no protocol or library git does not use.
fetch "https://curl.se/download/curl-$curl_version.tar.xz" "$curl_sha256"
(
  cd "curl-$curl_version"
  ./configure --prefix="$prefix" --enable-static --disable-shared --with-openssl \
    --without-ca-bundle --without-ca-path --with-ca-fallback \
    --without-libpsl --without-libidn2 --without-nghttp2 --without-nghttp3 \
    --without-ngtcp2 --without-brotli --without-zstd --without-libssh2 \
    --disable-ldap --disable-docs --disable-manual --enable-threaded-resolver
  make -j"$(nproc)" install
)
fetch "https://mirrors.edge.kernel.org/pub/software/scm/git/git-$git_version.tar.xz" "$git_sha256"
(
  cd "git-$git_version"
  # RUNTIME_PREFIX: git finds its helpers and templates relative to itself,
  # wherever cheesehost unpacks it.
  # NO_REGEX: musl's regex lacks the REG_STARTEND git needs; git's own then.
  make -j"$(nproc)" prefix=/ gitexecdir=libexec/git-core RUNTIME_PREFIX=YesPlease \
    NO_REGEX=NeedsStartEnd NO_RUST=YesPlease \
    NO_GETTEXT=YesPlease NO_TCLTK=YesPlease NO_PERL=YesPlease NO_PYTHON=YesPlease \
    NO_INSTALL_HARDLINKS=YesPlease INSTALL_SYMLINKS=YesPlease \
    CURL_CONFIG="$prefix/bin/curl-config" \
    CURL_LDFLAGS="$(pkg-config --static --libs libcurl)" LDFLAGS=-static \
    DESTDIR="$work/git-root" install
)

license() { # source dir, files...
  directory=$1
  shift
  for file in "$@"; do cp "$directory/$file" "$package/licenses/$directory-$(basename "$file")"; done
}

archive() {
  strip "$package"/bin/* 2>/dev/null || true
  tar -czf "$out/$(basename "$package")-$platform.tar.gz" -C "$(dirname "$package")" "$(basename "$package")"
  (cd "$out" && sha256sum "$(basename "$package")-$platform.tar.gz" > "$(basename "$package")-$platform.tar.gz.sha256")
  cat "$out/$(basename "$package")-$platform.tar.gz.sha256"
}

package="$work/host-tools-$revision"
mkdir -p "$package/bin" "$package/licenses" "$package/mkfs"
cp "iproute2-$iproute2_version/ip/ip" "$package/bin/ip"
cp "iptables-$iptables_version/iptables/xtables-nft-multi" "$package/bin/"
ln -s xtables-nft-multi "$package/bin/iptables-save"
ln -s xtables-nft-multi "$package/bin/iptables-restore"
cp "xfsprogs-$xfsprogs_version/mkfs/mkfs.xfs" "xfsprogs-$xfsprogs_version/quota/xfs_quota" "$package/bin/"
# The feature sets of each long-term kernel: a newer mkfs's defaults can make a
# filesystem an older kernel cannot mount, so the machine picks the file for
# the kernel it runs (`enrollment.DATA_DISK`).
cp "xfsprogs-$xfsprogs_version"/mkfs/lts_*.conf "$package/mkfs/"
license "iproute2-$iproute2_version" COPYING
license "iptables-$iptables_version" COPYING
license "libnftnl-$libnftnl_version" COPYING
license "xfsprogs-$xfsprogs_version" LICENSES/GPL-2.0
license "inih-$inih_version" LICENSE.txt
static "$package/bin/ip" "$package/bin/xtables-nft-multi" "$package/bin/mkfs.xfs" "$package/bin/xfs_quota"
"$package/bin/ip" -V
"$package/bin/iptables-restore" --version
"$package/bin/mkfs.xfs" -V
archive

package="$work/tmux-$revision"
mkdir -p "$package/bin" "$package/licenses"
cp "tmux-$tmux_version/tmux" "$package/bin/"
license "tmux-$tmux_version" COPYING
static "$package/bin/tmux"
"$package/bin/tmux" -V
archive

package="$work/git-$revision"
mv "$work/git-root" "$package"
# What a room's git does: no server side, no mail, and no dumb-HTTP push.
rm -rf "$package/share/man" "$package/share/gitweb" "$package/share/perl5" \
  "$package/bin/git-shell" "$package/bin/scalar" \
  "$package/libexec/git-core/git-daemon" "$package/libexec/git-core/git-http-backend" \
  "$package/libexec/git-core/git-imap-send" "$package/libexec/git-core/git-http-push" \
  "$package/libexec/git-core/git-http-fetch" "$package/libexec/git-core/git-shell" \
  "$package/libexec/git-core/scalar"
find "$package/libexec" -type f -perm -u+x -exec sh -c \
  'readelf -h "$1" >/dev/null 2>&1 && strip "$1"' _ {} \;
mkdir -p "$package/licenses"
license "git-$git_version" COPYING
license "curl-$curl_version" COPYING
static "$package/bin/git" "$package/libexec/git-core/git-remote-http"
"$package/bin/git" --version
archive
