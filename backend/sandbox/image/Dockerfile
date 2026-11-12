FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2

ENV DEBIAN_FRONTEND=noninteractive
COPY template-packages.tsv /usr/share/cheese/template-packages.tsv
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && echo '1500c1f56fa9e26b9b8f42452a553675796ade0807cdce11975eb98170b3a570  /etc/apt/keyrings/docker.asc' | sha256sum -c - \
    && echo 'deb [signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian trixie stable' > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends $(cut -f1 /usr/share/cheese/template-packages.tsv) build-essential gdb \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* \
    && rm -f /etc/ssh/ssh_host_* /etc/machine-id /var/lib/dbus/machine-id \
    && dpkg-query -W -f='${binary:Package}\t${Version}\n' > /usr/share/cheese/image-packages.tsv

RUN curl -fsSL https://github.com/jj-vcs/jj/releases/download/v0.46.0/jj-v0.46.0-x86_64-unknown-linux-musl.tar.gz -o /tmp/jj.tar.gz \
    && echo 'fce0271158e665ceb82dc66c5eb95b1728649094e9133074f3b5baece345fc08  /tmp/jj.tar.gz' | sha256sum -c - \
    && tar -xzf /tmp/jj.tar.gz -C /usr/local/bin ./jj \
    && rm /tmp/jj.tar.gz

LABEL org.opencontainers.image.source="https://github.com/SageSeekerSociety/cheese"
