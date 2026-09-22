# aivoicemail-worker: the worker service and the aivoicemail CLI (also used by the "tools" service).
FROM python:3.12-slim-trixie@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-client sip-tester libcap2-bin \
 && setcap cap_net_raw+ep /usr/bin/sipp \
 && apt-get purge -y --auto-remove libcap2-bin \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -g 5060 aivm \
 && useradd -u 10001 -g 5060 -M -d /var/lib/aivoicemail -s /usr/sbin/nologin aivm \
 && install -d -o 10001 -g 5060 -m 0750 /var/lib/aivoicemail \
 && install -d -o 10001 -g 5060 -m 0700 /run/aivoicemail
WORKDIR /opt/aivoicemail
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY pyproject.toml README.md LICENSE ./
COPY aivoicemail ./aivoicemail
RUN pip install --no-cache-dir --no-deps . && rm -rf build
COPY prompts ./prompts
ENV PYTHONUNBUFFERED=1 HF_HOME=/var/lib/aivoicemail/models AIVOICEMAIL_CONFIG=/opt/aivoicemail/config/aivoicemail.toml
USER 10001:5060
ENTRYPOINT ["aivoicemail"]
CMD ["worker"]
