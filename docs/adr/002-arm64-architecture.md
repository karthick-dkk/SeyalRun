# ADR-002: ARM64 (aarch64) Lab Architecture

**Date:** 2026-05-16
**Status:** Accepted

## Context

Server 1 (192.168.64.2) runs Ubuntu 24.04 on aarch64 (ARM64) — an Apple Silicon VM (UTM or Parallels). The README does not mention this constraint. Several design decisions are affected:

## Decision

Build all sidecar Docker images for `linux/arm64`. Use base images that have verified ARM64 support.

## Image Choices

| Layer | Image | ARM64 support |
|---|---|---|
| Python sidecars | `python:3.12-slim-bookworm` | Yes — official multi-arch |
| FreeIPA | `freeipa/freeipa-server:fedora-41` | Yes — Fedora ARM64 |
| Frontend (Vue) | `nginxinc/nginx-unprivileged:alpine` | Yes — multi-arch |
| **REJECTED** | `gcr.io/distroless/python3-debian12` | Limited ARM64 support |
| **REJECTED** | `almalinux` / `rockylinux` FreeIPA | x86_64 only |

## Build Command

```bash
docker buildx build --platform linux/arm64 -t pravesh/{service}:latest services/{service}/
```

## CI Note

GitHub Actions hosted runners are x86_64. CI builds images for `linux/amd64` for speed. ARM64 images are cross-compiled locally or on Server 1 for deployment.

## Consequences

- Developers on Intel Macs must use `--platform linux/arm64` when building images for Server 1
- GitHub Actions CI uses `linux/amd64` for build/test; add `linux/arm64` build to release workflow
- Some binary Python packages (e.g., numpy, grpcio) require ARM64 wheels — verify on first build
