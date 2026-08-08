# Build context: repo root (so future shared frontend libs could be added)
FROM node:24-alpine AS build
WORKDIR /build
COPY services/frontend/package.json services/frontend/package-lock.json* ./
RUN npm config set fetch-retries 5 \
 && npm config set fetch-retry-mintimeout 20000 \
 && npm config set fetch-retry-maxtimeout 120000 \
 && npm install
COPY services/frontend/ ./
# Release version shown in the UI sidebar (AppShell.vue) — set by
# docker-publish.yml from the pushed git tag; "dev" for local/untagged builds.
ARG APP_VERSION=dev
ENV VITE_APP_VERSION=$APP_VERSION
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
# The base image already defaults to a non-root user (that's the point of
# "-unprivileged"), so apk needs an explicit root switch to write its database.
# Picks up Alpine security patches published after the base image tag was last
# built upstream (e.g. CVE-2026-31789 in libssl3/libcrypto3) — apk upgrade alone
# isn't a one-time fix, it's what makes every rebuild pull current patches.
USER root
RUN apk update && apk upgrade --no-cache
COPY services/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html

# Already the base image's own default (confirmed via docker inspect) — declared
# explicitly here too since Trivy's Dockerfile linter (DS-0002) only reads this
# file's own text, not the inherited base-image user.
USER nginx
EXPOSE 8080
