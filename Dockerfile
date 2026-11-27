# Build arguments for image selection (defaults to DHI)
ARG BUILD_IMAGE=dhi.io/python:3.15-alpine3.24-sfw-dev
ARG RUNTIME_IMAGE=dhi.io/python:3.15-alpine3.24

# Deno binary source (for yt-dlp YouTube extraction)
# See: https://github.com/yt-dlp/yt-dlp/wiki/EJS
#
# Deno ships as a glibc binary with its loader hardcoded to /lib64. Repoint its
# ELF interpreter and rpath at the bundled glibc so it runs self-contained,
# without a system /lib64 loader. This lets it exec on the musl Alpine runtime
# (DHI) while NOT hijacking the system loader on a glibc runtime (local compose
# uses a Debian python slim image) — copying glibc over /lib64 breaks the latter.
# Written out, not an ARG: Dependabot only reads a FROM that names its image.
FROM denoland/deno:alpine-2.9.8 AS deno_src
# hadolint ignore=DL3018  # patchelf is a build-only tool; pinning it across deno base bumps is fragile
RUN apk add --no-cache patchelf \
 && patchelf --set-interpreter /usr/local/lib/glibc/ld-linux-x86-64.so.2 \
             --set-rpath /usr/local/lib/glibc /bin/deno

# uv turns uv.lock into a hashed requirements list for the builder's pip.
# Written out, not an ARG, for the same Dependabot reason as deno above.
FROM ghcr.io/astral-sh/uv:0.13.10 AS uv_src

# Stage 1: Build dependencies
FROM ${BUILD_IMAGE} AS builder

WORKDIR /app

# Install the locked runtime dependencies to an isolated directory. pip stays the
# installer because the -dev image wraps it in Socket Firewall; uv only exports the
# lock. The export is the whole dependency closure, every pin hashed, so pip checks
# each download and resolves nothing itself (--no-deps).
COPY --from=uv_src /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --output-file requirements.txt \
 && pip3 install --no-cache-dir --require-hashes --no-deps --target=/deps -r requirements.txt

# An empty directory for the runtime's /data, created here because the runtime image
# runs no build commands.
RUN mkdir /data-dir

# Stage 2: Runtime
FROM ${RUNTIME_IMAGE}

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/deps:/app/src
ENV PATH="/deps/bin:$PATH"

WORKDIR /app

# Expose HTTP server port
EXPOSE 8000

# Install Deno runtime (required for yt-dlp YouTube extraction). The binary was
# patched (in deno_src) to use its own bundled glibc, so it execs on the musl
# Alpine runtime without a system /lib64 loader and without disturbing one.
COPY --from=deno_src /bin/deno /usr/local/bin/deno
COPY --from=deno_src /usr/local/lib/glibc /usr/local/lib/glibc

# Copy installed dependencies from builder
COPY --from=builder /deps /deps

# The DHI runtime image can ship an older CA bundle than its -dev variant (it still
# trusted the compromised eMudhra intermediates after -dev had the fixed package), so
# take the builder's. The runtime's /etc/ssl/cert.pem is a symlink to this file.
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

# Copy application package
COPY src/webcam_aggregator/ ./src/webcam_aggregator/

# The store lives in /data, owned by the runtime user so it can write there; mount a
# host directory over it to keep the store across a container recreate.
COPY --from=builder --chown=65532:65532 /data-dir /data

# Run as a non-root user (defense-in-depth). Numeric UID works on both the DHI
# runtime (nonroot is 65532) and the local python:slim image (no /etc/passwd entry
# needed). HOME=/tmp gives yt-dlp/deno a writable cache dir. The app writes its store
# to /data (to /tmp when it can't), and /app/src + /deps are world-readable.
ENV HOME=/tmp
USER 65532:65532

# Health check (uses stdlib urllib - no curl/wget in DHI images)
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD ["/bin/sh", "-c", "python3 -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health')\" || exit 1"]

CMD ["python3", "-m", "webcam_aggregator"]
