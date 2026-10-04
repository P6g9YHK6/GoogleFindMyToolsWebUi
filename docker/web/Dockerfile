# Build context is the repo root: docker build -f docker/web/Dockerfile .

# Minifies webui/static/*.css|js and vendors htmx/Leaflet/webfonts locally
# (see package.json, scripts/copy-vendor.mjs) instead of the old unpkg.com
# CDN tags in base.html - this is the only place Node touches this image, the
# output (webui/static/dist/, webui/static/vendor/) is plain static files
# copied into the real runtime stage below, and Node itself never ships in
# the final image.
FROM node:20-slim AS assets
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY webui/static ./webui/static
COPY scripts/copy-vendor.mjs ./scripts/copy-vendor.mjs
RUN npm run build

FROM python:3.11-slim

WORKDIR /app

# requirements.lock is the fully pinned, hash-checked resolution of
# requirements.txt + requirements-web.txt (see CONTRIBUTING.md for how to
# regenerate it) - installing from it instead of the loose *.txt files means
# a rebuild of this exact commit always gets the exact same dependency
# versions, not whatever happens to be newest on the day it's built.
COPY requirements.lock /app/
RUN pip install --no-cache-dir -r requirements.lock

# Only what's needed to run apt-get itself at runtime (ca-certificates for the
# HTTPS downloads, procps for pkill during teardown), to actually install
# xvfb/x11vnc/novnc/websockify on demand, and git to clone ESP-IDF on demand
# (see webui/esp_idf_provisioning.py). Chrome and the ESP-IDF toolchain itself
# are both fetched at runtime, not via apt, and neither the browser/X11/VNC
# stack nor ESP-IDF is baked into this image - see webui/browser_provisioning.py
# and webui/esp_idf_provisioning.py.
#
# libusb-1.0-0 is ESP-IDF's own runtime dependency, not this app's: the
# openocd-esp32 tool that install.sh pulls in dlopen's libusb at startup, and
# idf_tools.py export sanity-checks every installed tool by running it -
# without this, that check fails ("error while loading shared libraries:
# libusb-1.0.so.0") and export aborts before printing anything to source env
# vars from, even though openocd (JTAG debugging) itself is never actually
# used by a build/merge-bin here.
#
# cmake/ninja-build/ccache/dfu-util/libffi-dev/libssl-dev/wget/flex/bison/
# gperf are ESP-IDF's own documented Linux prerequisites - install.sh only
# fetches the cross-compiler toolchains and debug tools (xtensa-esp32-elf,
# riscv32-esp-elf, openocd, gdb, ...), it never bundles cmake/ninja
# themselves, those are expected to already be on the system. Without them,
# `idf.py set-target` fails outright ("cmake" must be available on the PATH).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates procps tzdata git libusb-1.0-0 \
        cmake ninja-build ccache dfu-util libffi-dev libssl-dev wget flex bison gperf \
    && rm -rf /var/lib/apt/lists/*

COPY . /app
# Built assets land after the plain COPY above so they always win over
# whatever (if anything) is checked in at these paths - see .gitignore.
COPY --from=assets /app/webui/static/dist /app/webui/static/dist
COPY --from=assets /app/webui/static/vendor /app/webui/static/vendor

# GFMT_SECRETS_DIR is deliberately not set here - Auth/token_cache.py falls
# back to GFMT_DATA_DIR when it's unset, so everything we expect you to back
# up (auth.yaml alongside config.yaml/forwarding.yaml/forward.log/...) lands
# flat in one directory, no subfolders, matching the single `-v .../data:/data`
# mount below. The ~2.5GB on-demand ESP-IDF toolchain and firmware-build
# sandboxes are deliberately NOT in there - they live under
# GFMT_FIRMWARE_DIR=/firmware (see webui/config.py), in the image's writable
# layer by default with no mount, so they're ephemeral unless you opt in to a
# `-v .../firmware:/firmware` mount (docker-compose.yml, the Unraid template).
# Rebuildable at any time and auto-reclaimed after a disuse TTL - nothing there
# is worth backing up.
#
# GFMT_BUILD_SHA/GFMT_BUILD_DATE/GFMT_BUILD_BRANCH identify what's actually
# running (shown in the web UI's footer, see webui/templating.py) without the
# running container having to read back its own image's OCI labels. Only set
# by .github/workflows/docker-publish.yml via --build-arg; a plain local
# `docker build` (or docker-compose.dev.yml) leaves all three at their
# defaults, which the footer treats as "no real commit to link to" / "no
# branch to show".
ARG GFMT_BUILD_SHA=dev
ARG GFMT_BUILD_DATE=
ARG GFMT_BUILD_BRANCH=
# Written to a real file, not just an ENV - an ENV's cache key is its
# resolved text, which should already bust a stale hit on its own, but this
# has nonetheless been observed served stale from the registry buildcache
# (seen 2026-08-22 and again 2026-10-04: the footer kept showing a commit
# from a day-plus and several pushes earlier, despite /app itself having the
# current code). A RUN that only echoes the ARGs (the previous fix attempt
# here) produces an empty filesystem diff regardless of their value, which a
# content-addressed cache can still treat as equivalent across builds - this
# RUN's actual diff (the file's bytes) differs whenever the ARGs do, which a
# content-addressed cache can't collapse into a stale hit no matter what.
# webui/config.py reads this file, falling back to the env vars below only
# when it's absent entirely (a non-Docker `python -m webui.serve`).
RUN printf '%s\n%s\n%s\n' "${GFMT_BUILD_SHA}" "${GFMT_BUILD_DATE}" "${GFMT_BUILD_BRANCH}" > /app/.build_info
ENV DISPLAY=:99 \
    GFMT_BROWSER_RUNTIME_DIR=/run/gfmt-browser \
    GFMT_DATA_DIR=/data \
    GFMT_FIRMWARE_DIR=/firmware \
    GFMT_NONINTERACTIVE=1 \
    GFMT_BUILD_SHA=${GFMT_BUILD_SHA} \
    GFMT_BUILD_DATE=${GFMT_BUILD_DATE} \
    GFMT_BUILD_BRANCH=${GFMT_BUILD_BRANCH}

EXPOSE 4321

# See webui/main.py's /health route and docker/web/healthcheck.py - a plain
# Python script rather than curl, which isn't part of this image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python3", "docker/web/healthcheck.py"]

# webui/serve.py wraps the same plain uvicorn invocation the CMD used to be
# directly, adding a self-signed HTTPS option (HTTPS_ENABLED, see
# webui/config.py and webui/tls.py) - a no-op wrapper unless that's set.
CMD ["python3", "-m", "webui.serve"]
