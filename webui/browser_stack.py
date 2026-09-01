"""Installing, launching, and tearing down the on-demand Chrome/Xvfb/x11vnc/
noVNC stack a browser-based Google sign-in needs - process lifecycle only,
no idea of the sign-in state machine built on top of it (that's
webui/browser_provisioning.py). Progress is reported through an optional
on_progress(phase, message, percent) callback instead of this module knowing
anything about provisioning's own state or WebSocket broadcast - see
browser_provisioning.py's _set_state, which is what it's called with there.
"""

import asyncio
import json
import logging
import os
import time
import urllib.request
import zipfile
from collections.abc import Awaitable, Callable

from webui import config
from webui.progress import ProgressCallback, _no_progress

logger = logging.getLogger("webui.browser_stack")

CHROME_FOR_TESTING_JSON_URL = (
    "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"
)
X_PACKAGES = ["xvfb", "x11vnc", "novnc", "websockify"]
# Chrome for Testing ships as a bare binary (download_chrome) - these shared
# libs it dlopen's at startup have to come from apt or launch fails with
# "Chrome was not detected" despite the binary being right there.
CHROME_DEPS = [
    "fonts-liberation", "libasound2", "libatk-bridge2.0-0", "libatk1.0-0",
    "libcups2", "libdrm2", "libgbm1", "libgtk-3-0", "libnspr4", "libnss3",
    "libpango-1.0-0", "libpangocairo-1.0-0", "libx11-xcb1", "libxcomposite1",
    "libxdamage1", "libxfixes3", "libxkbcommon0", "libxrandr2", "xdg-utils",
]
# Default _wait() fallback for quick calls with no explicit timeout - apt/dpkg
# calls use _run_with_idle_timeout instead (see its own docstring).
_SUBPROCESS_TIMEOUT_S = 180

_processes: dict[str, asyncio.subprocess.Process] = {}
_chrome_bin: str | None = None


def _runtime_dir() -> str:
    d = config.GFMT_BROWSER_RUNTIME_DIR
    os.makedirs(d, exist_ok=True)
    return d


async def _force_kill(proc: asyncio.subprocess.Process):
    """Kills a process and reaps it, swallowing the race where it's already
    exited by the time we get here."""
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    await proc.wait()


async def _wait(proc: asyncio.subprocess.Process, timeout: float = _SUBPROCESS_TIMEOUT_S) -> int:
    """Waits for a subprocess with a hard timeout, killing it if it hangs,
    instead of letting a stuck apt lock/pkill/etc. block forever."""
    try:
        return await asyncio.wait_for(proc.wait(), timeout=timeout)
    except TimeoutError:
        logger.warning("Subprocess timed out after %ss, killing it", timeout)
        await _force_kill(proc)
        return -1


async def _run_with_idle_timeout(
    *args: str, env: dict[str, str], on_line: Callable[[str], Awaitable[None]] | None = None
) -> tuple[int, list[str]]:
    """Runs a subprocess, killing it only if it goes
    config.GFMT_BROWSER_APT_IDLE_TIMEOUT_S seconds without a single new line
    of output - not if the whole thing just runs long. A ~19-package install
    can legitimately take far longer than any fixed deadline as long as it's
    still producing "Unpacking"/"Setting up" lines; only a stuck dpkg
    lock/dead mirror going fully silent should be caught.

    Returns every line seen (so a failure can quote apt/dpkg's own error
    text) and the exit code, or -1 if killed for going quiet. Logs its own
    start/finish at INFO (lands on the Logs page) so a slow-but-working run
    and a genuinely stuck one leave a distinguishable trail."""
    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *args, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    logger.info("Running %s (pid %s)...", " ".join(args), proc.pid)
    lines: list[str] = []
    assert proc.stdout is not None  # always spawned with stdout=PIPE above
    idle_timeout = config.GFMT_BROWSER_APT_IDLE_TIMEOUT_S
    while True:
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=idle_timeout)
        except TimeoutError:
            logger.warning(
                "%s produced no output for %ss (%.1fs since it started; %d lines seen, last: %r) - killing it",
                args[0], idle_timeout, time.monotonic() - start, len(lines), lines[-1] if lines else None,
            )
            await _force_kill(proc)
            return -1, lines
        if not line:
            break
        text = line.decode(errors="replace").strip()
        if text:
            lines.append(text)
            if on_line:
                await on_line(text)
    rc = await _wait(proc, timeout=10)  # stdout closed -> should exit almost immediately
    logger.info(
        "%s finished in %.1fs with exit code %s (%d lines of output)",
        args[0], time.monotonic() - start, rc, len(lines),
    )
    return rc, lines


async def _packages_installed(packages: list[str]) -> bool:
    """dpkg -s reports non-zero if any of these packages is missing or only
    partially configured, so this doubles as a single cheap "is there
    anything left to do" check before touching apt/the network at all."""
    proc = await asyncio.create_subprocess_exec(
        "dpkg", "-s", *packages,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    rc = await _wait(proc, timeout=10)
    return rc == 0


async def _dpkg_configure_pending():
    """A previous install killed partway through (timeout, container
    stop/OOM) leaves dpkg "interrupted", failing every apt-get call after it
    until this is run - no shell into the container to do it by hand, so run
    it ourselves before every attempt. Fast no-op when nothing's pending."""
    await _run_with_idle_timeout(
        "dpkg", "--configure", "-a",
        env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
    )


async def install_x_stack(on_progress: ProgressCallback = _no_progress):
    packages = [*X_PACKAGES, *CHROME_DEPS]
    await _dpkg_configure_pending()

    # teardown() doesn't purge these, so after the first attempt they're
    # already here - skip apt entirely rather than confirm what we know.
    if await _packages_installed(packages):
        logger.info("All %d browser-stack packages already installed, skipping apt.", len(packages))
        await on_progress("installing", "X server, VNC tools, and Chrome dependencies already installed.", 35)
        return

    logger.info("Installing %d browser-stack packages via apt: %s", len(packages), ", ".join(packages))
    await on_progress("installing", "Updating package lists...", 5)
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}

    update_rc, _ = await _run_with_idle_timeout("apt-get", "update", env=env)
    if update_rc not in (0, -1):
        # Not fatal alone (install below can still work against a cached
        # index), but flagged since it's the likely cause if that fails too.
        logger.warning(
            "apt-get update exited with code %s - package lists may be stale for the install that follows",
            update_rc,
        )

    await on_progress("installing", f"Installing X server and VNC tools... (0/{len(packages)})", 8)

    # Turns apt's "Setting up <pkg>" lines into incremental phase updates
    # instead of one static message for the whole install.
    total = len(packages)
    installed = 0
    base_percent, cap_percent = 8, 33

    async def _on_line(text: str):
        nonlocal installed
        if not text.startswith("Setting up "):
            return
        installed += 1
        # "Setting up libgtk-3-0:amd64 (3.24.38-2ubuntu1) ..." -> "libgtk-3-0"
        name = text[len("Setting up "):].split(" ", 1)[0].split(":")[0]
        # Transitive deps not in our list also print this, so installed can
        # exceed total - clamp both the percent and displayed counter.
        percent = min(base_percent + round(installed / total * (cap_percent - base_percent)), cap_percent)
        await on_progress(
            "installing",
            f"Installing X server and VNC tools... ({min(installed, total)}/{total}: {name})",
            percent,
        )

    rc, output_lines = await _run_with_idle_timeout(
        "apt-get", "install", "-y", "--no-install-recommends", *packages, env=env, on_line=_on_line,
    )
    if rc == -1:
        # An idle-timeout kill, not an apt-get failure - say so plainly
        # rather than showing the output tail as if it were an error.
        raise RuntimeError(
            f"apt-get install of xvfb/x11vnc/novnc/websockify/chrome-deps produced no output for "
            f"{config.GFMT_BROWSER_APT_IDLE_TIMEOUT_S}s and was killed as stuck - check the "
            f"container's network access to its package mirror, or a stuck dpkg/apt lock."
        )
    if rc != 0:
        # The Config page only sees the last 10 lines below - log the full
        # transcript at ERROR too, so it's on the Logs page either way.
        logger.error("apt-get install failed (exit code %s); full output:\n%s", rc, "\n".join(output_lines))
        tail = "\n".join(output_lines[-10:])
        detail = f": {tail}" if tail else ""
        raise RuntimeError(f"apt-get install of xvfb/x11vnc/novnc/websockify/chrome-deps failed{detail}")

    await on_progress("installing", "X server and VNC tools installed.", 35)


async def download_chrome(on_progress: ProgressCallback = _no_progress) -> str:
    global _chrome_bin

    # teardown() doesn't delete chrome-linux64 between attempts - reuse a
    # previous fetch instead of re-downloading/re-extracting.
    cached_bin = os.path.join(_runtime_dir(), "chrome-linux64", "chrome")
    if os.path.exists(cached_bin) and os.access(cached_bin, os.X_OK):
        await on_progress("extracting", "Chrome already downloaded.", 70)
        _chrome_bin = cached_bin
        return cached_bin

    await on_progress("downloading", "Looking up the latest Chrome for Testing build...", 40)

    def _fetch_json():
        with urllib.request.urlopen(CHROME_FOR_TESTING_JSON_URL, timeout=30) as resp:
            return json.load(resp)

    data = await asyncio.to_thread(_fetch_json)
    stable = data["channels"]["Stable"]
    version = stable["version"]
    entry = next(d for d in stable["downloads"]["chrome"] if d["platform"] == "linux64")
    url = entry["url"]

    runtime_dir = _runtime_dir()
    zip_path = os.path.join(runtime_dir, "chrome-linux64.zip")

    await on_progress("downloading", f"Downloading Chrome for Testing {version}...", 45)
    try:
        # A background thread can't be killed on timeout the way _wait()
        # kills a subprocess - urlretrieve keeps running after this raises,
        # harmlessly: it only writes zip_path, which the next attempt
        # overwrites before extracting.
        await asyncio.wait_for(
            asyncio.to_thread(urllib.request.urlretrieve, url, zip_path),
            timeout=config.GFMT_BROWSER_DOWNLOAD_TIMEOUT_S,
        )
    except TimeoutError:
        raise RuntimeError(
            f"Timed out after {config.GFMT_BROWSER_DOWNLOAD_TIMEOUT_S}s downloading Chrome for "
            f"Testing - check the container's network access to storage.googleapis.com."
        ) from None

    await on_progress("extracting", "Extracting Chrome...", 65)

    def _extract():
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(runtime_dir)
        os.remove(zip_path)

    await asyncio.to_thread(_extract)

    chrome_bin = os.path.join(runtime_dir, "chrome-linux64", "chrome")
    os.chmod(chrome_bin, 0o755)

    await on_progress("extracting", "Chrome ready.", 70)
    _chrome_bin = chrome_bin
    return chrome_bin


async def _spawn_checked(name: str, *args: str):
    """Starts a tracked process and confirms it's still running a moment
    later - a stale process from a previous unclean shutdown still holding
    this one's port (:99/5900/6901) makes it exit immediately, which
    otherwise sails on to "ready" with a VNC iframe that can never connect."""
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    _processes[name] = proc
    await asyncio.sleep(1)
    if proc.returncode is not None:
        raise RuntimeError(
            f"{name} exited immediately after starting (exit code {proc.returncode}). A stale "
            f"process from a previous unclean shutdown may still be holding its port - try "
            f"restarting the container."
        )


async def start_x_stack(on_progress: ProgressCallback = _no_progress):
    await on_progress("launching", "Starting virtual display...", 75)
    await _spawn_checked(
        "xvfb",
        # undetected_chromedriver forces --window-size=1920,1080 on the
        # Chrome it launches, and there's no window manager here to resize
        # it back down - a smaller Xvfb screen would just crop it. Match
        # Xvfb's resolution so the full window is visible over VNC.
        "Xvfb", ":99", "-screen", "0", "1920x1080x24", "-nolisten", "tcp",
    )

    await on_progress("launching", "Starting VNC server...", 82)
    await _spawn_checked("x11vnc", "x11vnc", "-display", ":99", "-nopw", "-forever", "-shared", "-rfbport", "5900")

    await on_progress("launching", "Starting noVNC proxy...", 88)
    await _spawn_checked("websockify", "websockify", "--web=/usr/share/novnc", "6901", "localhost:5900")


async def kill_chrome() -> str | None:
    """pkill only *sends* the signal - it doesn't confirm the target actually
    exited - so poll briefly for it to actually be gone before deciding
    whether it needs reporting as a warning. Returns "Chrome" if it never
    went away, else None."""
    if not _chrome_bin:
        return None
    try:
        proc = await asyncio.create_subprocess_exec("pkill", "-f", _chrome_bin)
        await _wait(proc, timeout=5)
    except FileNotFoundError:
        return None

    for _ in range(10):  # ~5s total
        check = await asyncio.create_subprocess_exec(
            "pgrep", "-f", _chrome_bin,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        rc = await _wait(check, timeout=1)
        if rc != 0:  # pgrep: 0 = still found a match, 1 = none found
            return None
        await asyncio.sleep(0.5)
    return "Chrome"


async def _kill_tracked(name: str, proc: asyncio.subprocess.Process) -> str | None:
    if proc.returncode is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            return None
    return name if await _wait(proc, timeout=5) == -1 else None


async def teardown() -> list[str]:
    """Stops the chrome/Xvfb/x11vnc/websockify processes only - deliberately
    leaves the apt packages and downloaded Chrome binary in place for this
    container's life, so the next sign-in skips straight past
    install_x_stack/download_chrome. Safe indefinitely: every real image
    update replaces the container outright, discarding the cache anyway.

    Returns the names of whichever processes had to be force-killed.
    """
    global _chrome_bin

    # Concurrent, not sequential, so teardown finishes in one ~5s window
    # instead of up to 5s per process - within Docker's stop grace period.
    results = await asyncio.gather(
        kill_chrome(),
        *(_kill_tracked(name, proc) for name, proc in _processes.items()),
    )
    _processes.clear()
    _chrome_bin = None

    return [name for name in results if name]
