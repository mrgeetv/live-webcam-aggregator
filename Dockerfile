# Build arguments for image selection (defaults to DHI)
ARG BUILD_IMAGE=dhi.io/python:3.14-alpine3.24-sfw-dev
ARG RUNTIME_IMAGE=dhi.io/python:3.14-alpine3.24
ARG DENO_IMAGE=denoland/deno:alpine-2.8.3

# Deno binary source (for yt-dlp YouTube extraction)
# See: https://github.com/yt-dlp/yt-dlp/wiki/EJS
#
# Deno ships as a glibc binary with its loader hardcoded to /lib64. Repoint its
# ELF interpreter and rpath at the bundled glibc so it runs self-contained,
# without a system /lib64 loader. This lets it exec on the musl Alpine runtime
# (DHI) while NOT hijacking the system loader on a glibc runtime (local compose
# uses python:3.14-slim / Debian) — copying glibc over /lib64 breaks the latter.
FROM ${DENO_IMAGE} AS deno_src
# hadolint ignore=DL3018  # patchelf is a build-only tool; pinning it across deno base bumps is fragile
RUN apk add --no-cache patchelf \
 && patchelf --set-interpreter /usr/local/lib/glibc/ld-linux-x86-64.so.2 \
             --set-rpath /usr/local/lib/glibc /bin/deno

# Stage 1: Build dependencies
FROM ${BUILD_IMAGE} AS builder

WORKDIR /app

# Copy requirements and install dependencies to isolated directory
COPY requirements.txt .
RUN pip3 install --no-cache-dir --target=/deps -r requirements.txt

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

# Copy application package
COPY src/webcam_aggregator/ ./src/webcam_aggregator/

# Run as a non-root user (defense-in-depth). Numeric UID works on both the DHI
# runtime (nonroot is 65532) and the local python:slim image (no /etc/passwd entry
# needed). HOME=/tmp gives yt-dlp/deno a writable cache dir — the app itself writes
# nothing to disk, and /app/src + /deps are world-readable.
ENV HOME=/tmp
USER 65532:65532

# Health check (uses stdlib urllib - no curl/wget in DHI images)
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["python3", "-m", "webcam_aggregator"]
