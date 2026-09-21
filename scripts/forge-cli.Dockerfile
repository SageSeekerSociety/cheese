# Mount the repository at /work and run scripts/build-forge-cli.sh.
FROM rust:1.98.1-bookworm@sha256:93ce27a88655056a51dbdd8f5f2d7ddc071c7b0070fb288a37b5a285fc83971e
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc-aarch64-linux-gnu libc6-dev-arm64-cross qemu-user python3 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
CMD ["bash", "scripts/build-forge-cli.sh"]
