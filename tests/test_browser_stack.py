"""Tests for webui/browser_stack.py - installing/launching/killing the
Chrome/Xvfb/x11vnc/noVNC stack itself, independent of the sign-in state
machine built on top of it (see tests/test_browser_provisioning.py)."""

import asyncio
import time
import urllib.request

import pytest

from webui import browser_stack, config


class _FakeProc:
    """Stands in for an asyncio.subprocess.Process for kill_chrome's own
    pkill/pgrep calls (which it spawns internally), so this never shells out
    for real."""

    def __init__(self, returncode=0):
        self.returncode = returncode
        self.pid = 12345  # _run_with_idle_timeout logs this; never a real pid here

    async def wait(self):
        return self.returncode


async def test_kill_chrome_confirms_clean_exit(monkeypatch):
    browser_stack._chrome_bin = "/fake/chrome"

    async def fake_exec(*args, **kwargs):
        if args[0] == "pkill":
            return _FakeProc(0)
        if args[0] == "pgrep":
            return _FakeProc(1)  # not found -> gone
        raise AssertionError(f"unexpected exec {args}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert await browser_stack.kill_chrome() is None


async def test_kill_chrome_reports_stuck_process(monkeypatch):
    browser_stack._chrome_bin = "/fake/chrome"

    async def fake_exec(*args, **kwargs):
        if args[0] == "pkill":
            return _FakeProc(0)
        if args[0] == "pgrep":
            return _FakeProc(0)  # still found, every poll
        raise AssertionError(f"unexpected exec {args}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    t0 = time.monotonic()
    result = await browser_stack.kill_chrome()
    dt = time.monotonic() - t0
    assert result == "Chrome"
    assert dt < 6  # bounded polling window, not an indefinite hang


async def test_teardown_reports_a_process_that_ignores_sigterm():
    browser_stack._chrome_bin = None  # isolate to the tracked-process path only
    browser_stack._processes.clear()

    proc = await asyncio.create_subprocess_exec(
        "python3", "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
    )
    try:
        await asyncio.sleep(0.3)  # let it install the signal handler before we terminate() it
        browser_stack._processes["stubborn"] = proc

        t0 = time.monotonic()
        unclean = await browser_stack.teardown()
        dt = time.monotonic() - t0

        assert dt < 6  # concurrent kill/wait, not stacked sequentially
        assert unclean == ["stubborn"]
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def test_teardown_reports_nothing_when_everything_exits_cleanly():
    browser_stack._chrome_bin = None
    browser_stack._processes.clear()

    proc = await asyncio.create_subprocess_exec("sleep", "30")  # sleep exits on plain SIGTERM
    browser_stack._processes["well-behaved"] = proc

    assert await browser_stack.teardown() == []


async def test_teardown_clears_tracked_state():
    browser_stack._chrome_bin = "/fake/chrome"
    browser_stack._processes.clear()

    proc = await asyncio.create_subprocess_exec("sleep", "30")
    browser_stack._processes["well-behaved"] = proc

    await browser_stack.teardown()

    assert browser_stack._chrome_bin is None
    assert browser_stack._processes == {}


async def test_download_chrome_times_out_with_a_clear_message(monkeypatch, tmp_path):
    import json as json_module

    monkeypatch.setattr(config, "GFMT_BROWSER_DOWNLOAD_TIMEOUT_S", 0.05)
    monkeypatch.setattr(browser_stack, "_runtime_dir", lambda: str(tmp_path))

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json_module.dumps({
                "channels": {"Stable": {"version": "1.2.3", "downloads": {"chrome": [
                    {"platform": "linux64", "url": "http://example.invalid/chrome-linux64.zip"},
                ]}}}
            }).encode()

    def fake_urlretrieve(url, path):
        time.sleep(0.5)  # comfortably longer than the timeout above

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: _FakeResponse())
    monkeypatch.setattr(urllib.request, "urlretrieve", fake_urlretrieve)

    with pytest.raises(RuntimeError, match="Timed out after 0.05s downloading Chrome"):
        await browser_stack.download_chrome()


class _FakeStdout:
    """Stands in for a subprocess's StreamReader so install_x_stack's
    idle-timeout read loop (_run_with_idle_timeout) can readline() over
    canned output without a real apt-get/dpkg. An optional per-line delay
    lets a test simulate real elapsed time between lines without an actual
    subprocess."""

    def __init__(self, lines: list[str], line_delay: float = 0):
        self._lines = [f"{line}\n".encode() for line in lines]
        self._line_delay = line_delay

    async def readline(self):
        if self._line_delay:
            await asyncio.sleep(self._line_delay)
        return self._lines.pop(0) if self._lines else b""


class _FakeHangingStdout:
    """A stdout that never produces a line - simulates apt/dpkg going
    completely silent (a dead mirror, a stuck lock)."""

    async def readline(self):
        await asyncio.sleep(10)  # comfortably longer than any test's idle timeout
        return b""  # never actually reached in a passing test


class _FakeAptProc(_FakeProc):
    def __init__(self, returncode, output_lines: list[str], line_delay: float = 0):
        super().__init__(returncode)
        self.stdout = _FakeStdout(output_lines, line_delay=line_delay)
        self._killed = False

    async def wait(self):
        if self._killed:
            return self.returncode
        # Neither this nor the fake readline() above ever actually suspends
        # by default, so without a real yield here _run_with_idle_timeout's
        # read loop would never get a turn to drain stdout before this
        # returns.
        await asyncio.sleep(0)
        return self.returncode

    def kill(self):
        self._killed = True


class _FakeStuckAptProc(_FakeAptProc):
    """An apt-get install that never produces output and never exits on its
    own - only _run_with_idle_timeout's own timeout ends it, the same way a
    dead mirror or stuck dpkg lock would."""

    def __init__(self):
        super().__init__(returncode=-9, output_lines=[])
        self.stdout = _FakeHangingStdout()

    async def wait(self):
        if self._killed:
            return self.returncode
        await asyncio.sleep(10)
        return self.returncode


async def test_install_x_stack_runs_dpkg_configure_before_anything_else(monkeypatch):
    """A prior apt-get install that got killed mid-way (by _run_with_idle_timeout's
    own timeout, or the container itself being stopped) leaves dpkg
    interrupted, and every apt-get call after that fails immediately until
    'dpkg --configure -a' runs - see webui/browser_stack.py's
    _dpkg_configure_pending. That has to happen before even the dpkg -s "is
    it already installed" check, since an interrupted dpkg can make that
    check unreliable too."""
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        if args == ("dpkg", "--configure", "-a"):
            return _FakeAptProc(0, [])
        if args[:2] == ("dpkg", "-s"):
            return _FakeProc(0)  # already installed -> short-circuits before apt-get
        raise AssertionError(f"unexpected exec {args}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await browser_stack.install_x_stack()

    assert calls[0] == ("dpkg", "--configure", "-a")


async def test_install_x_stack_surfaces_apt_gets_own_error_in_the_failure(monkeypatch):
    """A generic "apt-get install failed" tells a user with no shell into the
    container nothing actionable - the raised error has to include apt/dpkg's
    own output so the real cause (a broken mirror, an interrupted dpkg, a
    missing package) is visible on the Config page itself."""
    async def fake_exec(*args, **kwargs):
        if args == ("dpkg", "--configure", "-a"):
            return _FakeAptProc(0, [])
        if args[:2] == ("dpkg", "-s"):
            return _FakeProc(1)  # not installed yet -> proceed to apt-get
        if args[:2] == ("apt-get", "update"):
            return _FakeAptProc(0, [])
        if args[:2] == ("apt-get", "install"):
            return _FakeAptProc(100, [
                "E: dpkg was interrupted, you must manually run 'dpkg --configure -a' to correct the problem.",
            ])
        raise AssertionError(f"unexpected exec {args}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(RuntimeError, match="dpkg was interrupted"):
        await browser_stack.install_x_stack()


async def test_install_x_stack_does_not_kill_a_slow_but_progressing_install(monkeypatch):
    """A slow disk/mirror that's still producing output shouldn't ever be
    killed just because the whole install runs long - only going quiet
    should trigger a kill. Ten lines 0.05s apart sum to well past a 0.2s
    idle timeout, but no single gap between lines does."""
    monkeypatch.setattr(config, "GFMT_BROWSER_APT_IDLE_TIMEOUT_S", 0.2)

    async def fake_exec(*args, **kwargs):
        if args == ("dpkg", "--configure", "-a"):
            return _FakeAptProc(0, [])
        if args[:2] == ("dpkg", "-s"):
            return _FakeProc(1)  # not installed yet -> proceed to apt-get
        if args[:2] == ("apt-get", "update"):
            return _FakeAptProc(0, [])
        if args[:2] == ("apt-get", "install"):
            return _FakeAptProc(0, [f"Setting up pkg{i} (1.0) ..." for i in range(10)], line_delay=0.05)
        raise AssertionError(f"unexpected exec {args}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    await browser_stack.install_x_stack()  # should complete without raising


async def test_install_x_stack_kills_a_stuck_install_after_going_quiet(monkeypatch):
    """apt-get producing zero output at all (a dead mirror, a stuck dpkg
    lock) has to be caught and reported honestly as having gone quiet, not
    confused with an actual apt-get failure."""
    monkeypatch.setattr(config, "GFMT_BROWSER_APT_IDLE_TIMEOUT_S", 0.05)

    async def fake_exec(*args, **kwargs):
        if args == ("dpkg", "--configure", "-a"):
            return _FakeAptProc(0, [])
        if args[:2] == ("dpkg", "-s"):
            return _FakeProc(1)
        if args[:2] == ("apt-get", "update"):
            return _FakeAptProc(0, [])
        if args[:2] == ("apt-get", "install"):
            return _FakeStuckAptProc()
        raise AssertionError(f"unexpected exec {args}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(RuntimeError, match="produced no output for"):
        await browser_stack.install_x_stack()


async def test_start_x_stack_raises_when_a_process_dies_immediately(monkeypatch):
    browser_stack._processes.clear()

    async def fake_exec(*args, **kwargs):
        # xvfb "starts" fine; x11vnc is the one a stale process left dead on arrival.
        return _FakeProc(returncode=None if args[0] == "Xvfb" else 1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    try:
        with pytest.raises(RuntimeError, match="x11vnc exited immediately"):
            await browser_stack.start_x_stack()
    finally:
        # The fake procs stashed in the module-level _processes dict don't
        # have a real terminate()/wait() - clear them out so a later test's
        # app-shutdown teardown (if it ever runs with a stale "active" phase
        # left over from some other test) doesn't trip over them.
        browser_stack._processes.clear()
