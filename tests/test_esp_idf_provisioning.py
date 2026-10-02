"""Tests for webui/esp_idf_provisioning.py - cloning/installing ESP-IDF on
demand, independent of the build state machine built on top of it (see
tests/test_firmware.py). Follows tests/test_browser_stack.py's _FakeProc
style, extended with communicate()."""

import asyncio
import os
import time

import webui.esp_idf_provisioning as esp_idf_provisioning
from webui import config


class _FakeStdout:
    """Stands in for a StreamReader for _run_git_clone's chunked read() loop -
    yields the given chunks in order, then an empty bytes to signal EOF."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    async def read(self, n=-1):
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _FakeProc:
    """Stands in for an asyncio.subprocess.Process for both _run_checked/
    get_env's communicate()-based calls and _run_git_clone's wait()+streamed-
    stdout-read() calls."""

    def __init__(self, returncode=0, output=b"", stdout_chunks: list[bytes] | None = None):
        self.returncode = returncode
        self._output = output
        self.stdout = _FakeStdout(stdout_chunks or [])

    async def communicate(self):
        return self._output, None

    async def wait(self):
        return self.returncode

    def kill(self):
        pass


def _patch_dirs(monkeypatch, tmp_path):
    idf_dir = tmp_path / "esp-idf"
    tools_dir = tmp_path / "esp-idf-tools"
    monkeypatch.setattr(config, "GFMT_ESP_IDF_DIR", idf_dir)
    monkeypatch.setattr(config, "GFMT_ESP_IDF_TOOLS_DIR", tools_dir)
    return idf_dir, tools_dir


def test_is_provisioned_false_when_marker_missing(monkeypatch, tmp_path):
    idf_dir, tools_dir = _patch_dirs(monkeypatch, tmp_path)
    (idf_dir / "tools").mkdir(parents=True)
    (idf_dir / "tools" / "idf.py").write_text("")
    tools_dir.mkdir()

    assert esp_idf_provisioning.is_provisioned() is False


def test_is_provisioned_true_when_marker_and_idf_py_present(monkeypatch, tmp_path):
    idf_dir, tools_dir = _patch_dirs(monkeypatch, tmp_path)
    (idf_dir / "tools").mkdir(parents=True)
    (idf_dir / "tools" / "idf.py").write_text("")
    tools_dir.mkdir()
    (tools_dir / ".gfmt-provisioned").write_text("")

    assert esp_idf_provisioning.is_provisioned() is True


async def test_provision_skips_clone_when_already_provisioned(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(esp_idf_provisioning, "is_provisioned", lambda: True)

    async def fake_exec(*args, **kwargs):
        raise AssertionError("should never spawn a subprocess when already provisioned")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    events = []

    async def on_progress(phase, message, percent):
        events.append(phase)

    await esp_idf_provisioning.provision(on_progress=on_progress)

    assert events == ["provisioning"]


async def test_provision_clones_and_installs_when_missing(monkeypatch, tmp_path):
    idf_dir, tools_dir = _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(esp_idf_provisioning, "is_provisioned", lambda: False)

    git_progress = b"Receiving objects:  50% (5/10)\rReceiving objects: 100% (10/10), done.\n"
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        if args[0] == "git":
            return _FakeProc(0, stdout_chunks=[git_progress])
        return _FakeProc(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    events = []

    async def on_progress(phase, message, percent):
        events.append((phase, message, percent))

    await esp_idf_provisioning.provision(on_progress=on_progress)

    phases = [e[0] for e in events]
    assert phases == ["cloning", "cloning", "cloning", "installing_toolchain", "provisioning"]
    # The two mid-clone updates parsed out of git's \r-separated progress
    # output - this is the part that used to just sit on one static message
    # for the whole multi-minute clone.
    assert any("50%" in e[1] for e in events)
    assert any("100%" in e[1] for e in events)

    assert calls[0][0] == "git"
    assert "clone" in calls[0]
    assert "--progress" in calls[0]
    assert esp_idf_provisioning.IDF_BRANCH in calls[0]
    assert str(idf_dir) in calls[0]
    assert calls[1][0] == str(idf_dir / "install.sh")
    assert esp_idf_provisioning.IDF_TARGETS in calls[1]
    assert (tools_dir / ".gfmt-provisioned").exists()


async def test_provision_raises_and_leaves_no_marker_on_install_failure(monkeypatch, tmp_path):
    idf_dir, tools_dir = _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(esp_idf_provisioning, "is_provisioned", lambda: False)

    async def fake_exec(*args, **kwargs):
        if args[0] == "git":
            return _FakeProc(0)
        return _FakeProc(1, output=b"install.sh: missing dependency")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    try:
        await esp_idf_provisioning.provision()
        raised = False
    except RuntimeError as e:
        raised = True
        assert "missing dependency" in str(e)

    assert raised
    assert not (tools_dir / ".gfmt-provisioned").exists()


async def test_get_env_parses_key_value_output(monkeypatch, tmp_path):
    idf_dir, tools_dir = _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    # idf_tools.py export's own output never re-states IDF_TOOLS_PATH (it's
    # an input to it, not a derived var) - regression test for the bug where
    # that meant idf.py fell back to its default ~/.espressif at build time.
    output = b'PATH="/fake/idf/tools:$PATH"\nIDF_PATH="/fake/idf"\n'

    async def fake_exec(*args, **kwargs):
        return _FakeProc(0, output=output)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    env = await esp_idf_provisioning.get_env()

    assert env["IDF_PATH"] == "/fake/idf"
    assert env["IDF_TOOLS_PATH"] == str(tools_dir)
    # PATH="...:$PATH" is a literal shell-variable reference meant for eval,
    # not an already-expanded value - regression test for the bug where the
    # literal "$PATH" text was kept as-is, silently dropping /usr/bin (cmake,
    # ninja, git, ...) off the front of it entirely.
    assert env["PATH"] == "/fake/idf/tools:/usr/bin:/bin"


def test_cleanup_if_stale_noop_when_ttl_disabled(monkeypatch, tmp_path):
    idf_dir, tools_dir = _patch_dirs(monkeypatch, tmp_path)
    idf_dir.mkdir()
    tools_dir.mkdir()
    (tools_dir / ".gfmt-provisioned").write_text("")
    monkeypatch.setattr(config, "GFMT_ESP_IDF_IDLE_TTL_S", 0)

    assert esp_idf_provisioning.cleanup_if_stale() is False
    assert (tools_dir / ".gfmt-provisioned").exists()


def test_cleanup_if_stale_noop_when_not_provisioned(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "GFMT_ESP_IDF_IDLE_TTL_S", 1)
    assert esp_idf_provisioning.cleanup_if_stale() is False


def test_cleanup_if_stale_removes_stale_toolchain(monkeypatch, tmp_path):
    idf_dir, tools_dir = _patch_dirs(monkeypatch, tmp_path)
    (idf_dir / "tools").mkdir(parents=True)
    (idf_dir / "tools" / "idf.py").write_text("")
    marker = tools_dir / ".gfmt-provisioned"
    marker.parent.mkdir(parents=True)
    marker.write_text("")
    # Backdate the marker's mtime by 2 days.
    old = time.time() - 172800
    os.utime(marker, (old, old))
    monkeypatch.setattr(config, "GFMT_ESP_IDF_IDLE_TTL_S", 86400)  # 1 day

    assert esp_idf_provisioning.cleanup_if_stale() is True
    assert not idf_dir.exists()
    assert not tools_dir.exists()


def test_cleanup_if_stale_noop_when_fresh(monkeypatch, tmp_path):
    idf_dir, tools_dir = _patch_dirs(monkeypatch, tmp_path)
    idf_dir.mkdir()
    tools_dir.mkdir()
    (tools_dir / ".gfmt-provisioned").write_text("")  # fresh marker
    monkeypatch.setattr(config, "GFMT_ESP_IDF_IDLE_TTL_S", 2592000)  # 30 days

    assert esp_idf_provisioning.cleanup_if_stale() is False
    assert idf_dir.exists()
    assert tools_dir.exists()


def test_migrate_legacy_dirs_moves_old_install(monkeypatch, tmp_path):
    old_data = tmp_path / "data"
    fw = tmp_path / "firmware"
    (old_data / "esp-idf" / "tools").mkdir(parents=True)
    (old_data / "esp-idf" / "tools" / "idf.py").write_text("")
    (old_data / "esp-idf-tools").mkdir()
    monkeypatch.setattr(config, "DATA_DIR", old_data)
    monkeypatch.setattr(config, "GFMT_FIRMWARE_DIR", fw)

    esp_idf_provisioning.migrate_legacy_dirs()

    assert (fw / "esp-idf" / "tools" / "idf.py").exists()
    assert (fw / "esp-idf-tools").is_dir()
    assert not (old_data / "esp-idf").exists()
    assert not (old_data / "esp-idf-tools").exists()


def test_migrate_legacy_dirs_noop_when_nothing_to_migrate(monkeypatch, tmp_path):
    old_data = tmp_path / "data"
    old_data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", old_data)
    monkeypatch.setattr(config, "GFMT_FIRMWARE_DIR", tmp_path / "firmware")

    esp_idf_provisioning.migrate_legacy_dirs()  # must not raise
    assert (tmp_path / "firmware").exists() is False


def test_migrate_legacy_dirs_deletes_leftover_when_dest_exists(monkeypatch, tmp_path):
    old_data = tmp_path / "data"
    fw = tmp_path / "firmware"
    (old_data / "esp-idf-tools").mkdir(parents=True)
    (fw / "esp-idf-tools").mkdir(parents=True)
    monkeypatch.setattr(config, "DATA_DIR", old_data)
    monkeypatch.setattr(config, "GFMT_FIRMWARE_DIR", fw)

    esp_idf_provisioning.migrate_legacy_dirs()

    assert not (old_data / "esp-idf-tools").exists()
    assert (fw / "esp-idf-tools").exists()
