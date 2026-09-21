#!/usr/bin/env bash
# Build pinned fj artifacts with the merged-PR localization fix (upstream #694).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
version=0.6.0-cheese.2
build_dir="$root/tmp/forge-cli-$version"
mkdir -p "$build_dir"
artifacts="$build_dir/distribution"
mkdir -p "$artifacts"
exec > >(tee -a "$build_dir/build.log") 2>&1
log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*"; }
trap 'log "status=fail line=$LINENO"' ERR
export PATH="$HOME/.cargo/bin:$PATH"
export MACOSX_DEPLOYMENT_TARGET=12.0
export OPENSSL_STATIC=1
export RUSTFLAGS="--remap-path-prefix=$HOME=/build"
export CFLAGS="-ffile-prefix-map=$HOME=/build"
export CARGO_BUILD_JOBS=4
log "status=start version=$version rust=1.98.1 source=crates.io locked=true openssl=vendored"
case "$(uname -s)" in
  Darwin) targets=(aarch64-apple-darwin x86_64-apple-darwin) ;;
  Linux)
    targets=(x86_64-unknown-linux-gnu aarch64-unknown-linux-gnu)
    export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc
    export CC_aarch64_unknown_linux_gnu=aarch64-linux-gnu-gcc
    export AR_aarch64_unknown_linux_gnu=aarch64-linux-gnu-ar
    ;;
  *) exit 1 ;;
esac
rustup toolchain install 1.98.1 --profile minimal
archive="$build_dir/forgejo-cli-0.6.0.crate"
if [[ ! -f "$archive" ]]; then
  curl -fL https://static.crates.io/crates/forgejo-cli/forgejo-cli-0.6.0.crate -o "$archive.part"
  mv "$archive.part" "$archive"
fi
printf '%s  %s\n' 4d56acd6ab5caab2870d6e301cd6e42741ca98761fc1d5890dad09b21b44780e "$archive" | shasum -a 256 -c -
if [[ ! -d "$build_dir/forgejo-cli-0.6.0" ]]; then
  tar -xf "$archive" -C "$build_dir"
fi
cd "$build_dir/forgejo-cli-0.6.0"
if ! patch --force -R -p1 --dry-run < "$root/scripts/patches/forgejo-cli-merged-at.patch" >/dev/null 2>&1; then
  patch --force -p1 < "$root/scripts/patches/forgejo-cli-merged-at.patch"
fi
for target in "${targets[@]}"; do
  result="$artifacts/fj-$version-$target.tar.gz"
  if [[ -f "$result.sha256" ]]; then
    (cd "$artifacts" && shasum -a 256 -c "$result.sha256")
    log "item=$target status=skip reason=verified-artifact"
    continue
  fi
  log "item=$target status=start"
  rustup +1.98.1 target add "$target"
  cargo +1.98.1 build --release --locked --target "$target" --features git2/vendored-openssl
  binary="target/$target/release/fj"
  # A distributable binary may use system libraries, never Homebrew paths.
  if [[ "$target" == *apple-darwin ]]; then
    otool -L "$binary"
    if otool -L "$binary" | tail -n +2 | grep -Ev '^\s+(/usr/lib/|/System/Library/)' ; then
      log "item=$target status=fail reason=non-system-dynamic-library"
      exit 1
    fi
  fi
  if [[ "$target" == aarch64-unknown-linux-gnu ]]; then
    qemu-aarch64 -L /usr/aarch64-linux-gnu "$binary" --help
    python3 "$root/scripts/test-forge-cli-status.py" qemu-aarch64 -L /usr/aarch64-linux-gnu "$PWD/$binary"
  else
    "$binary" --help
    python3 "$root/scripts/test-forge-cli-status.py" "$PWD/$binary"
  fi
  # Vendored OpenSSL retains its compiled module directories. Those contain
  # only the build location; reject source paths and other home-directory data.
  # /root is the generic container build home, not a personal build path.
  if [[ "$HOME" != /root ]] && strings "$binary" | grep -F "$HOME" | grep -Ev '/openssl-build/install/lib/(engines-3|ossl-modules)$'; then
    log "item=$target status=fail reason=private-build-path"
    exit 1
  fi
  tar -czf "$result.part" LICENSE-APACHE LICENSE-MIT -C "target/$target/release" fj
  mv "$result.part" "$result"
  (cd "$artifacts" && shasum -a 256 "$(basename "$result")") > "$result.sha256"
  log "item=$target status=done artifact=$result"
done
log 'status=done'
