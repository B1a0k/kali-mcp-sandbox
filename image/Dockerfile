ARG KALI_BASE
FROM ${KALI_BASE}
ARG KALI_APT_MIRROR=http://kali.download/kali
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
# The optional CA bundle is build-only (for HTTPS on the minimal base / enterprise proxy).
# Packages remain authenticated by Kali's archive signing key; TLS verification is never disabled.
RUN --mount=type=secret,id=apt_ca,target=/run/secrets/apt-ca,mode=0444 \
    --mount=type=cache,target=/var/cache/apt/archives,sharing=locked \
    case "$KALI_APT_MIRROR" in https://*|http://*) ;; *) echo 'Invalid KALI_APT_MIRROR' >&2; exit 1;; esac \
    && if [ -f /run/secrets/apt-ca ]; then printf 'Acquire::https::CaInfo "/run/secrets/apt-ca";\n' > /etc/apt/apt.conf.d/98kali-mcp-build-ca; fi \
    && printf 'deb %s kali-rolling main contrib non-free non-free-firmware\n' "$KALI_APT_MIRROR" > /etc/apt/sources.list \
    && printf 'Dir::Etc::sourceparts "-";\n' > /etc/apt/apt.conf.d/99kali-mcp-mirror \
    && apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 -o APT::Update::Error-Mode=any update && apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 install -y --no-install-recommends \
    ca-certificates curl wget jq iproute2 dnsutils netcat-openbsd \
    nmap gobuster dirb nikto sqlmap whatweb python3 python3-flask python3-mcp \
    python3-requests python3-waitress \
    && dpkg-query -W > /usr/share/kali-mcp-package-versions.txt \
    && rm -rf /var/lib/apt/lists/* && rm -f /etc/apt/apt.conf.d/98kali-mcp-build-ca \
    && useradd --uid 1000 --create-home --shell /bin/bash kali \
    && mkdir -p /workspace && chown kali:kali /workspace
COPY upstream /opt/kali-mcp/upstream
COPY jobs.py api.py bridge.py worker.py /opt/kali-mcp/
USER 1000:1000
WORKDIR /workspace
ENTRYPOINT ["python3", "/opt/kali-mcp/api.py"]
