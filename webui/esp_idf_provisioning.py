"""On-demand ESP-IDF toolchain provisioning: cloning the esp-idf source and
installing its esp32/esp32-c3 toolchains into GFMT_FIRMWARE_DIR (see
webui/config.py), so `idf.py` builds work without baking the ~1-2GB toolchain
into the Docker image itself (see docker/web/Dockerfile - it only has git, not
ESP-IDF). Same on-demand-and-cached shape as webui/browser_stack.py's Chrome-
for-Testing download, but the cache is re-usable rather than per-attempt: it
lives under /firmware in the container (separate from the persisted /data
volume, and only mounted there if the user opts in - see docker-compose.yml),
and cleanup_if_stale() reclaims it after GFMT_ESP_IDF_IDLE_TTL_S of disuse so
an abandoned install doesn't hug ~2.5GB forever.

Pure mechanics only, no idea of the build's own state machine or websocket -
see webui/firmware_build.py, which calls into this and reports progress
through the same on_progress(phase, message, percent) callback shape
webui/browser_provisioning.py uses over browser_stack.py.
"""

import asyncio
import logging
import os
import pathlib
import re
import shutil
import time

from webui import config
from webui.progress import ProgressCallback, _no_progress

logger = logging.getLogger("webui.esp_idf_provisioning")

IDF_GIT_URL = "https://github.com/espressif/esp-idf.git"
# A maintained branch, not one fixed patch tag - matches the checked-in
# ESP32Firmware/sdkconfig's "ESP-IDF 5.1.0" major.minor. Only matters once,
# at the first clone below; nothing here ever re-pulls it afterwards, so this
# doesn't drift once a given container has provisioned itself.
IDF_BRANCH = "release/v5.1"
IDF_TARGETS = "esp32,esp32c3"

# Marker file written only after install.sh exits 0 - see is_provisioned()'s
# docstring for why this matters over just checking tools/idf.py exists.
_MARKER_NAME = ".gfmt-provisioned"


def _idf_dir() -> pathlib.Path:
    return config.GFMT_ESP_IDF_DIR


def _tools_dir() -> pathlib.Path:
    return config.GFMT_ESP_IDF_TOOLS_DIR


def is_provisioned() -> bool:
    """True only once install.sh has actually finished successfully - not
    just because tools/idf.py exists, which a killed/interrupted clone can
    also leave behind (e.g. the container getting stopped mid-clone)."""
    idf_py = _idf_dir() / "tools" / "idf.py"
    marker = _tools_dir() / _MARKER_NAME
    return idf_py.exists() and marker.exists()


def idf_py_path() -> str:
    return str(_idf_dir() / "tools" / "idf.py")


async def _run_checked(*args: str, cwd: str | None = None, env: dict | None = None,
                        timeout: float | None = None):
    """Runs one provisioning step to completion, raising RuntimeError (with
    the captured output tail) on a non-zero exit or timeout - same
    error-shape convention webui/firmware_build.py's _run_idf uses for build
    steps, just via communicate() instead of a streaming line-progress drain:
    there's nothing structured like ninja's "[123/456]" to parse out of git
    clone/install.sh output, so there's no reason to stream it incrementally."""
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=cwd, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"`{' '.join(args)}` timed out after {timeout}s") from None

    if proc.returncode != 0:
        tail = stdout.decode(errors="replace")[-4000:]
        raise RuntimeError(f"`{' '.join(args)}` exited with code {proc.returncode}\n{tail}")


# git prints "Receiving objects: 45% (5678/12345), 12.34 MiB | 3.45 MiB/s"-style
# progress by overwriting one line with \r, not \n - _run_git_clone below reads
# raw chunks and splits on both so these actually show up incrementally
# instead of only once the whole (multi-minute) clone finishes.
_GIT_PROGRESS_RE = re.compile(r"(Receiving objects|Resolving deltas):\s*(\d+)%")


async def _run_git_clone(args: list[str], timeout: float, on_progress: ProgressCallback,
                          phase: str, base_percent: int, cap_percent: int):
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    tail: list[str] = []

    async def _drain():
        buf = b""
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\r" in buf or b"\n" in buf:
                idx = min(i for i in (buf.find(b"\r"), buf.find(b"\n")) if i != -1)
                raw_line, buf = buf[:idx], buf[idx + 1:]
                text = raw_line.decode(errors="replace").strip()
                if not text:
                    continue
                tail.append(text)
                del tail[:-50]
                m = _GIT_PROGRESS_RE.search(text)
                if m:
                    pct = int(m.group(2))
                    percent = base_percent + round(pct / 100 * (cap_percent - base_percent))
                    await on_progress(phase, f"Downloading ESP-IDF... {m.group(1).lower()}: {pct}%",
                                       min(percent, cap_percent))
        if buf.strip():
            tail.append(buf.decode(errors="replace").strip())

    try:
        # Drain to EOF (which follows the process closing its stdout, i.e.
        # exiting) before reaping the exit code, rather than racing the two -
        # proc.wait() can resolve slightly before every buffered chunk has
        # actually been read out, which would cut the last progress update
        # (or, worse, the whole tail used for the error message below) off.
        await asyncio.wait_for(_drain(), timeout=timeout)
        rc = await proc.wait()
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"`{' '.join(args)}` timed out after {timeout}s") from None

    if rc != 0:
        raise RuntimeError(f"`{' '.join(args)}` exited with code {rc}\n" + "\n".join(tail[-20:]))


async def provision(on_progress: ProgressCallback = _no_progress):
    cleanup_if_stale()
    if is_provisioned():
        _mark_used()
        await on_progress("provisioning", "ESP-IDF already installed.", 15)
        return

    idf_dir = _idf_dir()
    tools_dir = _tools_dir()
    timeout = config.GFMT_ESP_IDF_PROVISION_TIMEOUT_S

    # A previous attempt may have been killed mid-clone/install - start clean
    # rather than risk install.sh operating on a half-cloned tree.
    await asyncio.to_thread(shutil.rmtree, idf_dir, True)
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / _MARKER_NAME).unlink(missing_ok=True)

    await on_progress(
        "cloning",
        f"Downloading ESP-IDF ({IDF_BRANCH})... this can take a few minutes the first time.",
        2,
    )
    await _run_git_clone(
        ["git", "clone", "--recursive", "--shallow-submodules", "--progress", "--depth", "1",
         "--branch", IDF_BRANCH, IDF_GIT_URL, str(idf_dir)],
        timeout=timeout, on_progress=on_progress, phase="cloning", base_percent=2, cap_percent=8,
    )

    await on_progress(
        "installing_toolchain",
        f"Installing ESP-IDF toolchain for {IDF_TARGETS}...",
        8,
    )
    await _run_checked(
        str(idf_dir / "install.sh"), IDF_TARGETS,
        cwd=str(idf_dir), env={**os.environ, "IDF_TOOLS_PATH": str(tools_dir)},
        timeout=timeout,
    )

    (tools_dir / _MARKER_NAME).write_text("")
    _mark_used()
    await on_progress("provisioning", "ESP-IDF ready.", 15)


def _mark_used():
    """Records that the toolchain was just used by bumping the marker's mtime.
    cleanup_if_stale() compares against this to decide whether the install has
    gone idle long enough to reclaim - so a container that provisions once and
    then builds regularly never gets swept out from under a live deployment."""
    marker = _tools_dir() / _MARKER_NAME
    try:
        if marker.exists():
            marker.touch()
    except OSError:
        logger.warning("Could not update ESP-IDF last-used marker", exc_info=True)


# The directory the toolchain used to live in, before it moved out of the
# backed-up DATA_DIR into GFMT_FIRMWARE_DIR (see webui/config.py).
_LEGACY_TOOLCHAIN_DIRS = ("esp-idf", "esp-idf-tools")


def migrate_legacy_dirs() -> None:
    """One-time relocation for installs provisioned before the toolchain moved
    out of DATA_DIR: a persistent volume from before that change still has
    esp-idf/ and esp-idf-tools/ (~2.5GB deck) sitting in the directory we're
    supposed to back up. Move them into GFMT_FIRMWARE_DIR on startup so an
    upgrade keeps its existing install without re-downloading, and the volume
    stops carrying a multi-GB, entirely-rebuildable install it was never meant
    to hold. Runs before cleanups in main.py's lifespan. Delete (rather than
    move) if a new install already exists under GFMT_FIRMWARE_DIR - the old
    one is throwaway either way."""
    for name in _LEGACY_TOOLCHAIN_DIRS:
        _relocate_legacy(config.DATA_DIR / name, name)


def _relocate_legacy(src: pathlib.Path, name: str) -> None:
    dst = config.GFMT_FIRMWARE_DIR / name
    if not src.exists() or src.resolve() == dst.resolve():
        return
    if dst.exists():
        logger.info("Removing leftover %s at %s (already exists under %s)", name, src, dst)
        shutil.rmtree(src, ignore_errors=True)
        return
    logger.info("Migrating %s from %s to %s", name, src, dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def cleanup_if_stale() -> bool:
    """Deletes a provisioned but long-unused ESP-IDF install to reclaim space -
    the whole ~2.5GB (source clone + toolchains) sits under GFMT_FIRMWARE_DIR
    and is entirely rebuildable at the next build via the marker-based
    is_provisioned() check. Only reclaims if the install is older than
    GFMT_ESP_IDF_IDLE_TTL_S (0 disables this entirely). Returns True if it
    deleted something. Called at app startup (webui/main.py) and defensively
    at the top of provision()."""
    ttl_s = config.GFMT_ESP_IDF_IDLE_TTL_S
    if ttl_s <= 0:
        return False
    idf_dir = _idf_dir()
    tools_dir = _tools_dir()
    marker = tools_dir / _MARKER_NAME
    if not (is_provisioned() if idf_dir.exists() else False):
        return False
    try:
        marker_mtime = marker.stat().st_mtime
    except OSError:
        return False
    if time.time() - marker_mtime <= ttl_s:
        return False

    logger.info("Reclaiming ESP-IDF toolchain idle for %ds (TTL %ds)", time.time() - marker_mtime, ttl_s)
    shutil.rmtree(idf_dir, ignore_errors=True)
    shutil.rmtree(tools_dir, ignore_errors=True)
    return True


async def get_env() -> dict:
    """The documented programmatic equivalent of sourcing ESP-IDF's export.sh
    (PATH additions for idf.py/the toolchains/the Python venv, IDF_PATH,
    etc.) - computed fresh before every build rather than cached in memory,
    so it always matches what's actually on disk. idf_tools.py itself is a
    fast local script, no network involved."""
    idf_dir = _idf_dir()
    tools_dir = _tools_dir()
    proc = await asyncio.create_subprocess_exec(
        "python3", str(idf_dir / "tools" / "idf_tools.py"), "export", "--format", "key-value",
        env={**os.environ, "IDF_TOOLS_PATH": str(tools_dir)},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"idf_tools.py export failed:\n{stdout.decode(errors='replace')}")

    # IDF_TOOLS_PATH itself is an input to idf_tools.py, not one of the
    # derived vars it prints - export's own output never re-states it, so
    # without setting it explicitly here idf.py falls back to its default
    # ~/.espressif (nothing installed there) instead of our DATA_DIR install.
    env = {**os.environ, "IDF_TOOLS_PATH": str(tools_dir)}
    for line in stdout.decode(errors="replace").splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"')
        if key == "PATH":
            # export's own PATH value is "<idf toolchain/venv dirs>:$PATH" -
            # a literal shell variable reference meant for `eval`ing inside
            # export.sh, not an already-expanded value. There's no shell in
            # the loop here to expand it, so without this substitution the
            # subprocess's PATH would end with a literal, meaningless
            # "$PATH" segment instead of the base PATH it's supposed to
            # extend - silently dropping /usr/bin (cmake, ninja, git, ...)
            # off the front of it entirely.
            value = value.replace("$PATH", env.get("PATH", ""))
        env[key] = value
    return env
