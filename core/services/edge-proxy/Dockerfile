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

# Already the base image's own default — declared explicitly for Trivy's
# Dockerfile linter (DS-0002), same as services/frontend/Dockerfile.
USER nginx
EXPOSE 8080 8443
