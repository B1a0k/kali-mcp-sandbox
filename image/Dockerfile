ARG KALI_BASE
FROM ${KALI_BASE}
ARG KALI_APT_MIRROR=http://kali.download/kali
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
ARG IMAGE_VERSION=development
# The optional CA bundle is build-only (for HTTPS on the minimal base / enterprise proxy).
# Packages remain authenticated by Kali's archive signing key; TLS verification is never disabled.
RUN --mount=type=secret,id=apt_ca,target=/run/secrets/apt-ca,mode=0444 \
    --mount=type=cache,target=/var/cache/apt/archives,sharing=locked \
    case "$KALI_APT_MIRROR" in https://*|http://*) ;; *) echo 'Invalid KALI_APT_MIRROR' >&2; exit 1;; esac \
    && if [ -f /run/secrets/apt-ca ]; then printf 'Acquire::https::CaInfo "/run/secrets/apt-ca";\n' > /etc/apt/apt.conf.d/98kali-mcp-build-ca; fi \
    && printf 'deb %s kali-rolling main contrib non-free non-free-firmware\n' "$KALI_APT_MIRROR" > /etc/apt/sources.list \
    && printf 'Dir::Etc::sourceparts "-";\n' > /etc/apt/apt.conf.d/99kali-mcp-mirror \
    && apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 -o APT::Update::Error-Mode=any update \
    && apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 upgrade -y --no-install-recommends \
    && apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 install -y --no-install-recommends \
    ca-certificates curl wget jq iproute2 iputils-ping dnsutils netcat-openbsd \
    procps psmisc tcpdump traceroute whois socat openssh-client sshpass \
    openvpn proxychains4 smbclient samba-common-bin ldap-utils krb5-user \
    python3-impacket impacket-scripts netexec enum4linux-ng smbmap hydra john \
    nmap gobuster dirb nikto sqlmap whatweb python3 python3-flask python3-mcp \
    python3-requests python3-waitress python3-pip python3-venv git \
    && ln -s /usr/bin/impacket-smbexec /usr/local/bin/smbexec \
    && dpkg-query -W > /usr/share/kali-mcp-package-versions.txt \
    && rm -rf /var/lib/apt/lists/* && rm -f /etc/apt/apt.conf.d/98kali-mcp-build-ca \
    && mkdir -p /workspace && chmod 0700 /workspace \
    && printf '%s\n' "$IMAGE_VERSION" > /etc/kali-mcp-image-version
COPY upstream /opt/kali-mcp/upstream
COPY jobs.py api.py bridge.py worker.py capabilities.py /opt/kali-mcp/
USER 0:0
WORKDIR /workspace
ENTRYPOINT ["python3", "/opt/kali-mcp/api.py"]
