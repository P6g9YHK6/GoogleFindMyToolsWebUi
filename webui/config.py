import os
import pathlib
import time

# noVNC/websockify run in this same container (webui/browser_provisioning.py),
# proxied through our own origin (webui/routers/vnc_proxy.py), not exposed directly.
BROWSER_NOVNC_URL = os.environ.get("BROWSER_NOVNC_URL", "http://localhost:6901")

# On-demand Chrome/Xvfb/noVNC stack - tmpfs, wiped on every container restart.
GFMT_BROWSER_RUNTIME_DIR = os.environ.get("GFMT_BROWSER_RUNTIME_DIR", "/run/gfmt-browser")
# How long an idle login attempt runs before the provisioned stack tears back down.
GFMT_BROWSER_IDLE_TIMEOUT_S = int(os.environ.get("GFMT_BROWSER_IDLE_TIMEOUT_S", "600"))
# urlretrieve has no timeout of its own - without this a blocked path to
# storage.googleapis.com hangs the sign-in flow forever with no error.
GFMT_BROWSER_DOWNLOAD_TIMEOUT_S = int(os.environ.get("GFMT_BROWSER_DOWNLOAD_TIMEOUT_S", "300"))
# Idle timeout, not total duration - apt-get/dpkg (browser_stack.py) can
# legitimately run long as long as it's still producing output; this only
# fires once it goes fully quiet (dead mirror, stuck dpkg lock).
GFMT_BROWSER_APT_IDLE_TIMEOUT_S = int(os.environ.get("GFMT_BROWSER_APT_IDLE_TIMEOUT_S", "300"))
DEFAULT_POLL_INTERVAL_S = int(os.environ.get("DEFAULT_POLL_INTERVAL_S", "300"))
# How long a device-list fetch is cached before re-fetching - webui/device_list_cache.py.
DEVICE_LIST_CACHE_TTL_S = float(os.environ.get("DEVICE_LIST_CACHE_TTL_S", "8"))
LOCATE_CONCURRENCY = int(os.environ.get("LOCATE_CONCURRENCY", "5"))
LOCATE_TIMEOUT_S = int(os.environ.get("LOCATE_TIMEOUT_S", "60"))

# Account-wide throttle for every blocking call to Google's backend - see
# webui/deps.py's run_blocking. At most QUERY_THROTTLE_MAX requests per
# rolling QUERY_THROTTLE_WINDOW_S window, plus QUERY_MIN_SPREAD_S seconds
# between any two. Over either limit waits its turn rather than failing. 0
# disables that limit.
QUERY_THROTTLE_MAX = int(os.environ.get("QUERY_THROTTLE_MAX", "20"))
QUERY_THROTTLE_WINDOW_S = float(os.environ.get("QUERY_THROTTLE_WINDOW_S", "60"))
QUERY_MIN_SPREAD_S = float(os.environ.get("QUERY_MIN_SPREAD_S", "1"))

# If both set, the whole web UI (incl. the WebSocket) requires HTTP Basic Auth.
HTTP_USER = os.environ.get("HTTP_USER")
HTTP_PASSWORD = os.environ.get("HTTP_PASSWORD")

# Opt-in self-signed HTTPS - see webui/tls.py. Exact "1" match, not truthy-string,
# so HTTPS_ENABLED=0 can't accidentally enable it.
HTTPS_ENABLED = os.environ.get("HTTPS_ENABLED") == "1"

# Public-showcase demo mode - see webui/demo_mode.py.
DEMO_MODE = os.environ.get("DEMO_MODE") == "1"
# Bring-your-own-cert - if either is set, webui/tls.py requires both files to
# exist (fails loudly instead of silently falling back to self-signed).
GFMT_TLS_CERT_PATH = os.environ.get("GFMT_TLS_CERT_PATH")
GFMT_TLS_KEY_PATH = os.environ.get("GFMT_TLS_KEY_PATH")
# Extra SANs (comma-separated) for the generated self-signed cert, beyond
# localhost/127.0.0.1/::1 - set to your LAN hostname/IP if you use either.
GFMT_TLS_SAN = os.environ.get("GFMT_TLS_SAN")
# Apple's ATS hard-caps accepted cert validity at 825 days, even for a
# manually-trusted one - longer just silently fails in Safari/iOS/macOS.
GFMT_TLS_VALIDITY_DAYS = int(os.environ.get("GFMT_TLS_VALIDITY_DAYS", "825"))

DATA_DIR = pathlib.Path(os.environ.get("GFMT_DATA_DIR") or (pathlib.Path(__file__).parent / "data"))

# One file, keyed by canonic device ID - see webui/device_store.py.
# FORWARDING_CONFIG_PATH/DEVICE_LOCATIONS_PATH/LATEST_VALUES_PATH/their legacy
# JSON below are the pre-fusion locations, read once to migrate then unused.
DEVICES_PATH = DATA_DIR / "devices.yaml"
FORWARDING_CONFIG_PATH = DATA_DIR / "forwarding.yaml"
FORWARDING_CONFIG_LEGACY_JSON_PATH = DATA_DIR / "forwarding_config.json"
FORWARD_LOG_PATH = DATA_DIR / "forward.log"
FORWARD_LOG_LEGACY_JSON_PATH = DATA_DIR / "forward_log.json"
FORWARD_LOG_MAX_ENTRIES = int(os.environ.get("FORWARD_LOG_MAX_ENTRIES", "1000"))
APP_SETTINGS_PATH = DATA_DIR / "config.yaml"

# Every INFO-or-above log record app-wide - see webui/log_capture.py.
SYSTEM_LOG_PATH = DATA_DIR / "system.log"
SYSTEM_LOG_MAX_ENTRIES = int(os.environ.get("SYSTEM_LOG_MAX_ENTRIES", "5000"))

DEVICE_LOCATIONS_PATH = DATA_DIR / "device_locations.yaml"
LATEST_VALUES_PATH = DATA_DIR / "latest_values.yaml"

# Every advertisement key ever produced by /register - see webui/firmware_store.py.
# Safe to persist: the public EID, not the private eik.
REGISTERED_TRACKERS_PATH = DATA_DIR / "registered_trackers.yaml"

# A cold ESP-IDF build can take several minutes - see webui/firmware_build.py.
GFMT_FIRMWARE_BUILD_TIMEOUT_S = int(os.environ.get("GFMT_FIRMWARE_BUILD_TIMEOUT_S", "900"))

# On-demand ESP-IDF toolchain - under DATA_DIR (the volume mount), since
# re-fetching ~1-2GB on every restart would be too slow. webui/esp_idf_provisioning.py.
GFMT_ESP_IDF_DIR = DATA_DIR / "esp-idf"
GFMT_ESP_IDF_TOOLS_DIR = DATA_DIR / "esp-idf-tools"
# Only paid once per DATA_DIR, not per build - generous for a slow connection.
GFMT_ESP_IDF_PROVISION_TIMEOUT_S = int(os.environ.get("GFMT_ESP_IDF_PROVISION_TIMEOUT_S", "1800"))

# Only used when GFMT_TLS_CERT_PATH/GFMT_TLS_KEY_PATH above aren't set.
TLS_CERT_PATH = DATA_DIR / "tls_cert.pem"
TLS_KEY_PATH = DATA_DIR / "tls_key.pem"

# Baked in at image build time - docker/web/Dockerfile,
# .github/workflows/docker-publish.yml. "dev" = local build, no --build-arg.
GFMT_BUILD_SHA = os.environ.get("GFMT_BUILD_SHA", "dev")
GFMT_BUILD_DATE = os.environ.get("GFMT_BUILD_DATE", "")
GFMT_BUILD_BRANCH = os.environ.get("GFMT_BUILD_BRANCH", "")

APP_START_TIME = time.monotonic()

# Process-wide DEMO_MODE network backstop - imported here (before virtually
# everything else) rather than in main.py's lifespan. No-op when unset.
from webui import demo_network_guard  # noqa: E402

demo_network_guard.install(DEMO_MODE)
