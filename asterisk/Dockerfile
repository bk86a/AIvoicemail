# aivoicemail-asterisk: inbound-only Asterisk 20 (Ubuntu 24.04 package), hardened base config baked in,
# trunk/dialplan/prompts mounted from generated/ at runtime.
FROM ubuntu:24.04@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3
ARG ASTERISK_VERSION=1:20.6.0~dfsg+~cs6.13.40431414-2build5
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "asterisk=${ASTERISK_VERSION}" \
 && rm -rf /var/lib/apt/lists/* /etc/asterisk/* \
 && groupmod -g 5060 asterisk && usermod -u 5060 -g 5060 asterisk \
 && mkdir -p /usr/lib/asterisk \
 && ln -sfn "$(ls -d /usr/lib/*-linux-gnu/asterisk/modules)" /usr/lib/asterisk/modules \
 && install -d -o 5060 -g 5060 -m 2770 /srv/aivoicemail/spool
COPY etc/ /etc/asterisk-base/
COPY bin/entrypoint bin/vm-finalize /usr/local/bin/
RUN chmod 0755 /usr/local/bin/entrypoint /usr/local/bin/vm-finalize
USER 5060:5060
ENTRYPOINT ["/usr/local/bin/entrypoint"]
