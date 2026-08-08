FROM nginxinc/nginx-unprivileged:1.27-alpine
# The base image already defaults to a non-root user (that's the point of
# "-unprivileged"), so apk needs an explicit root switch to write its database.
# Picks up Alpine security patches published after the base image tag was last
# built upstream (e.g. CVE-2026-31789 in libssl3/libcrypto3) — apk upgrade alone
# isn't a one-time fix, it's what makes every rebuild pull current patches.
USER root
RUN apk update && apk upgrade --no-cache

# Template (not a static conf): the base image's entrypoint runs envsubst over
# /etc/nginx/templates/*.template at boot, substituting ${FRAME_ANCESTORS} and
# leaving nginx's own $-vars intact, then writes /etc/nginx/conf.d/default.conf.
COPY services/edge-proxy/templates/default.conf.template /etc/nginx/templates/default.conf.template

# Placeholder for the internal CA. nginx parses proxy_ssl_trusted_certificate at
# config load and refuses to start if the file is missing or unparseable, so the
# path must always resolve to a valid certificate — even in the default
# deployment, where upstream verification is off and this file is never used.
# docker-compose.internal-tls.yml mounts the real CA over it.
RUN apk add --no-cache openssl \
    && mkdir -p /etc/nginx/ssl \
    && openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
         -keyout /tmp/placeholder-key.pem -out /etc/nginx/ssl/internal-ca.pem \
         -subj "/CN=SeyalRun placeholder - internal TLS not enabled" 2>/dev/null \
    && rm -f /tmp/placeholder-key.pem \
    && chmod 644 /etc/nginx/ssl/internal-ca.pem

# Already the base image's own default — declared explicitly for Trivy's
# Dockerfile linter (DS-0002), same as services/frontend/Dockerfile.
USER nginx
EXPOSE 8080 8443
