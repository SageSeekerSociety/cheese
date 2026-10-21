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
revision=2
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
# The libraries the programs above link statically come from Alpine's
# packages, which carry no license texts. Each text is taken from upstream at
# the version Alpine built (`linked` stops the build when Alpine has moved on).
musl_version=1.2.5
musl_copyright_sha256=f9bc4423732350eb0b3f7ed7e91d530298476f8fec0c6c427a1c04ade22655af
libmnl_version=1.0.5
libmnl_sha256=274b9b919ef3152bfb3da3a13c950dd60d6e2bcd54230ffeca298d03b40d0525
urcu_version=0.15.2
urcu_sha256=59f36f2b8bda1b7620a7eced2634f26c549444818a8313025a3bb09c0766a61d
util_linux_version=2.41.6
util_linux_sha256=e596083744e746be7d2823b62b43f4418dd7bf56303b4dc09e6fe8112fe3d7ed
libevent_version=2.1.13
libevent_sha256=f7e9383b8c0baa81b687e5b5eecc01beefaf1b19b64151d95ed61647fe7a315c
ncurses_version=6.5_p20250503
ncurses_copying_sha256=708999f95527e1ffa670c6fce288c6c600cb477dd04afcc1171422b3dd4ee226
openssl_version=3.5.9
openssl_license_sha256=7d5450cb2d142651b8afa315b5f238efc805dad827d91ba367d8516bc9d49e7a
zlib_version=1.3.2
zlib_license_sha256=e32ff4e00d9d94930537635291da39e7e612703334bf6fde8c7f1686fe8a45a2
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

# One file of an upstream source, into <directory>/<name>.
fetch_file() { # url sha256 directory name
  mkdir -p "$3"
  curl -fsSL --retry 5 --retry-all-errors -o "$3/$4" "$1"
  echo "$2  $3/$4" | sha256sum -c -
}

# The Alpine package a program links statically must be the version whose
# license text the recipe carries.
linked() { # package, version
  have=$(apk list -I "$1" | sed -n "s/^$1-\(.*\)-r[0-9]* .*/\1/p")
  if [ "$have" != "$2" ]; then
    echo "Alpine's $1 is ${have:-not installed}; the recipe's license text is for $2" >&2
    exit 1
  fi
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

# --- the license texts of the libraries linked from Alpine ----------------
linked musl-dev "$musl_version"
linked libmnl-static "$libmnl_version"
linked userspace-rcu-static "$urcu_version"
linked util-linux-static "$util_linux_version"
linked libevent-static "$libevent_version"
linked ncurses-static "$ncurses_version"
linked openssl-libs-static "$openssl_version"
linked zlib-static "$zlib_version"
fetch_file "https://git.musl-libc.org/cgit/musl/plain/COPYRIGHT?h=v$musl_version" \
  "$musl_copyright_sha256" "musl-$musl_version" COPYRIGHT
fetch "https://www.netfilter.org/pub/libmnl/libmnl-$libmnl_version.tar.bz2" "$libmnl_sha256"
fetch "https://lttng.org/files/urcu/userspace-rcu-$urcu_version.tar.bz2" "$urcu_sha256"
fetch "https://mirrors.edge.kernel.org/pub/linux/utils/util-linux/v${util_linux_version%.*}/util-linux-$util_linux_version.tar.xz" "$util_linux_sha256"
fetch "https://github.com/libevent/libevent/releases/download/release-$libevent_version-stable/libevent-$libevent_version-stable.tar.gz" "$libevent_sha256"
# ncurses is released as snapshots: 6.5_p20250503 is tag v6_5_20250503.
ncurses_tag=v$(echo "$ncurses_version" | sed 's/[._]p\{0,1\}/_/g')
fetch_file "https://raw.githubusercontent.com/ThomasDickey/ncurses-snapshots/$ncurses_tag/COPYING" \
  "$ncurses_copying_sha256" "ncurses-$ncurses_version" COPYING
fetch_file "https://raw.githubusercontent.com/openssl/openssl/openssl-$openssl_version/LICENSE.txt" \
  "$openssl_license_sha256" "openssl-$openssl_version" LICENSE.txt
fetch_file "https://raw.githubusercontent.com/madler/zlib/v$zlib_version/LICENSE" \
  "$zlib_license_sha256" "zlib-$zlib_version" LICENSE

# A component's license texts into the package's licenses/, and its line in
# the package's NOTICE.
license() { # component, version, SPDX license, source dir, files...
  printf '%-14s %-15s %s\n' "$1" "$2" "$3" >> "$package/NOTICE"
  directory=$4
  shift 4
  for file in "$@"; do cp "$directory/$file" "$package/licenses/$directory-$(basename "$file")"; done
}

notice() { # what the package is
  printf '%s\n\n%-14s %-15s %s\n' "$1" component version license > "$package/NOTICE"
  # Every program is linked statically against musl, Alpine's C library.
  license musl "$musl_version" MIT "musl-$musl_version" COPYRIGHT
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
notice "host-tools $revision: ip (iproute2), iptables (xtables-nft-multi), mkfs.xfs and xfs_quota (xfsprogs), linked statically from these components, built by scripts/build-host-tools.sh. Each component's license texts are under licenses/."
license iproute2 "$iproute2_version" GPL-2.0-or-later "iproute2-$iproute2_version" COPYING
license iptables "$iptables_version" GPL-2.0-or-later "iptables-$iptables_version" COPYING
license libnftnl "$libnftnl_version" GPL-2.0-or-later "libnftnl-$libnftnl_version" COPYING
license libmnl "$libmnl_version" LGPL-2.1-or-later "libmnl-$libmnl_version" COPYING
license xfsprogs "$xfsprogs_version" "GPL-2.0-only AND LGPL-2.1-only" "xfsprogs-$xfsprogs_version" LICENSES/GPL-2.0 LICENSES/LGPL-2.1
license inih "$inih_version" BSD-3-Clause "inih-$inih_version" LICENSE.txt
license userspace-rcu "$urcu_version" "LGPL-2.1-or-later AND MIT AND LicenseRef-Boehm-GC" \
  "userspace-rcu-$urcu_version" LICENSE.md LICENSES/LGPL-2.1-or-later.txt LICENSES/MIT.txt \
  LICENSES/LicenseRef-Boehm-GC.txt
license libuuid "$util_linux_version" BSD-3-Clause "util-linux-$util_linux_version" \
  Documentation/licenses/COPYING.BSD-3-Clause
license libblkid "$util_linux_version" LGPL-2.1-or-later "util-linux-$util_linux_version" \
  Documentation/licenses/COPYING.LGPL-2.1-or-later
static "$package/bin/ip" "$package/bin/xtables-nft-multi" "$package/bin/mkfs.xfs" "$package/bin/xfs_quota"
"$package/bin/ip" -V
"$package/bin/iptables-restore" --version
"$package/bin/mkfs.xfs" -V
archive

package="$work/tmux-$revision"
mkdir -p "$package/bin" "$package/licenses"
cp "tmux-$tmux_version/tmux" "$package/bin/"
notice "tmux $tmux_version, linked statically from these components, built by scripts/build-host-tools.sh. Each component's license texts are under licenses/."
license tmux "$tmux_version" ISC "tmux-$tmux_version" COPYING
license libevent "$libevent_version" BSD-3-Clause "libevent-$libevent_version-stable" LICENSE
license ncurses "$ncurses_version" X11 "ncurses-$ncurses_version" COPYING
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
notice "git $git_version, with https through its own libcurl, linked statically from these components, built by scripts/build-host-tools.sh. Each component's license texts are under licenses/."
# git is GPL-2.0; its own copy of the regex library it is built with here
# (NO_REGEX) is LGPL-2.1-or-later. Its SHA-1 (sha1dc, the default) and
# reftable code are compiled in under licenses of their own.
license git "$git_version" "GPL-2.0-only AND LGPL-2.1-or-later" "git-$git_version" COPYING LGPL-2.1
license sha1dc "(git $git_version)" MIT "git-$git_version" sha1dc/LICENSE.txt
license reftable "(git $git_version)" BSD-3-Clause "git-$git_version" reftable/LICENSE
license curl "$curl_version" curl "curl-$curl_version" COPYING
license openssl "$openssl_version" Apache-2.0 "openssl-$openssl_version" LICENSE.txt
license zlib "$zlib_version" Zlib "zlib-$zlib_version" LICENSE
static "$package/bin/git" "$package/libexec/git-core/git-remote-http"
"$package/bin/git" --version
archive
