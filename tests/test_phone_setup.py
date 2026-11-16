"""The phone as a whole installation: model choice, configuration, service, restore."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nanomuse.cli import app
from nanomuse.phone.setup import (
    DEFAULT_MODEL,
    MARKER,
    OLLAMA_BASE_URL,
    app_settings,
    boot_script,
    choose_model,
    config_toml,
    install_service,
    merge_app_settings,
    package_install,
    read_ram_gb,
    restore_export,
    service_script,
    write_config,
)

runner = CliRunner()


def plain(text: str) -> str:
    """Drop the escape codes a Rich console adds, so an assertion reads the words."""
    import re

    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


# ----------------------------------------------------------------------------- model


def test_read_ram_gb_from_meminfo(tmp_path: Path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       8123456 kB\nMemFree: 123 kB\n", encoding="utf-8")
    assert read_ram_gb(meminfo) == pytest.approx(8123456 / 1024 / 1024)
    assert read_ram_gb(tmp_path / "missing") == 0.0
    meminfo.write_text("garbage\n", encoding="utf-8")
    assert read_ram_gb(meminfo) == 0.0


def test_choose_a_model_that_fits_the_phone():
    assert choose_model(4).name == "qwen2.5:3b"
    assert choose_model(6).name == "qwen3:4b"
    assert choose_model(12).name == "qwen3:8b"
    # an unreadable memory size is treated as a 6 GB phone, not as "no model"
    assert choose_model(0).name == DEFAULT_MODEL
    # a model of the person's own is taken as named, even when the catalogue has never seen it
    custom = choose_model(8, "my-model:7b")
    assert custom.name == "my-model:7b"
    assert custom.download_gb == 0.0
    # the vision model is offered explicitly, not picked automatically
    assert choose_model(6, "qwen2.5vl:3b").vision is True
    assert choose_model(6).vision is False


# ----------------------------------------------------------------------------- config


def test_the_config_points_at_the_phone_and_closes_the_network(tmp_path: Path):
    data = tmp_path / "data"
    workspace = data / "workspace"
    text = config_toml(data, workspace, "qwen3:4b")
    parsed = tomllib.loads(text)
    assert parsed["data_dir"] == str(data)
    assert parsed["agent"]["workspace"] == str(workspace)
    assert parsed["llm"]["provider"] == "openai"
    assert parsed["llm"]["model"] == "qwen3:4b"
    assert parsed["llm"]["base_url"] == OLLAMA_BASE_URL
    assert parsed["llm"]["api_key"] == "ollama"
    assert parsed["llm"]["tool_mode"] == "auto"
    assert parsed["server"]["host"] == "127.0.0.1"
    assert parsed["server"]["port"] == 8787
    assert parsed["server"]["auth"] is True
    assert parsed["server"]["update_check"] is False
    assert parsed["cloud"] == {"required": False, "models": False, "sync": False}
    assert parsed["hub"]["enabled"] is False
    assert parsed["memory"]["embedding_model"] == "nomic-embed-text"
    assert parsed["memory"]["embedding_base_url"] == OLLAMA_BASE_URL
    assert text.startswith(MARKER)


def test_a_hand_written_config_is_kept_before_it_is_replaced(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('data_dir = "/hand/written"\n', encoding="utf-8")
    backup = write_config(path, config_toml(tmp_path, tmp_path / "workspace", "qwen3:4b"))
    assert backup == path.with_name("config.toml.before-phone")
    assert backup is not None and '"/hand/written"' in backup.read_text(encoding="utf-8")
    assert path.read_text(encoding="utf-8").startswith(MARKER)
    # a file this command wrote is replaced without another backup
    assert write_config(path, path.read_text(encoding="utf-8")) is None


def test_app_settings_select_the_local_model_and_keep_the_rest():
    existing = {
        "connectors": {"calendar": {"enabled": True}},
        "llm": {"model": "old", "api_key": "a-real-key", "proxy": "http://proxy"},
        "cloud_mode": "hybrid",
    }
    merged = app_settings(existing, "qwen3:4b")
    assert merged["llm"]["model"] == "qwen3:4b"
    assert merged["llm"]["base_url"] == OLLAMA_BASE_URL
    assert merged["llm"]["api_key"] == "ollama"
    assert merged["llm"]["provider"] == "openai"
    assert merged["cloud_mode"] == "local"
    assert merged["cloud"] == {"required": False, "models": False, "sync": False}
    # what the phone setup does not own stays as it was
    assert merged["connectors"] == {"calendar": {"enabled": True}}
    # the input is not changed behind the caller's back
    assert existing["llm"]["model"] == "old"


def test_merge_app_settings_writes_a_private_file(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "app-settings.json").write_text(
        json.dumps({"connectors": {"email": {"enabled": True}}}), encoding="utf-8"
    )
    merged = merge_app_settings(data / "app-settings.json", "qwen3:4b")
    saved = json.loads((data / "app-settings.json").read_text(encoding="utf-8"))
    assert saved["version"] == 1
    assert saved["llm"]["model"] == "qwen3:4b"
    assert saved["connectors"] == {"email": {"enabled": True}}
    assert merged["cloud_mode"] == "local"
    assert (data / "app-settings.json.before-phone").is_file()


# ----------------------------------------------------------------------------- service


def test_the_service_script_owns_both_processes():
    prefix = Path("/data/data/com.termux/files/usr")
    home = Path("/data/data/com.termux/files/home")
    text = service_script(prefix, home, model="qwen3:4b")
    assert text.startswith("#!/bin/bash")
    assert "PREFIX=/data/data/com.termux/files/usr" in text
    assert "HOME_DIR=/data/data/com.termux/files/home" in text
    assert "MODEL=qwen3:4b" in text
    assert "nohup ollama serve" in text
    assert 'proot-distro login "$DISTRO" --shared-home' in text
    assert "termux-wake-lock" in text
    assert "termux-wake-unlock" in text
    assert 'nohup "$0" _supervise' in text
    assert "phone restore /root/$_name" in text
    assert "up)" in text and "down)" in text and "status)" in text


def test_the_boot_script_starts_the_phone_after_a_restart():
    text = boot_script(Path("/data/data/com.termux/files/usr"), Path("/home/me"))
    assert "nanomuse-phone up" in text
    assert "/home/me/.nanomuse/logs/boot.log" in text
    assert MARKER in text


@pytest.mark.skipif(os.name != "posix" or not shutil.which("bash"), reason="needs bash")
def test_the_generated_scripts_are_valid_shell(tmp_path: Path):
    for name, text in (
        ("service.sh", service_script(Path("/tmp/prefix"), Path("/tmp/home"))),
        ("boot.sh", boot_script(Path("/tmp/prefix"), Path("/tmp/home"))),
    ):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_install_service_writes_the_command_and_the_boot_script(tmp_path: Path):
    prefix = tmp_path / "prefix"
    home = tmp_path / "home"
    written = install_service(prefix, home, model="qwen3:4b")
    assert [p.name for p in written] == ["nanomuse-phone", "nanomuse.sh"]
    command = prefix / "bin" / "nanomuse-phone"
    boot = home / ".termux" / "boot" / "nanomuse.sh"
    assert command.is_file() and boot.is_file()
    if os.name == "posix":
        assert command.stat().st_mode & 0o777 == 0o755
        assert boot.stat().st_mode & 0o777 == 0o755
    assert "nanomuse-phone up" in boot.read_text(encoding="utf-8")


# ----------------------------------------------------------------------------- restore


def test_restore_accepts_only_the_names_a_data_export_writes(tmp_path: Path):
    archive = tmp_path / "export.zip"
    data = tmp_path / "data"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("profile.json", '{"name": "Muse"}')
        z.writestr("threads/main.json", "[]")
        z.writestr("README.txt", "what this is")
        z.writestr("config.toml", "data_dir = '/somewhere/else'")
        z.writestr("app-settings.json", "{}")
        z.writestr("vault.key", "secret")
        z.writestr("../outside.txt", "no")
        link = zipfile.ZipInfo("threads/link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(link, "/etc/passwd")
    count = restore_export(archive, data)
    assert count == 2
    assert (data / "profile.json").read_text(encoding="utf-8") == '{"name": "Muse"}'
    assert (data / "threads" / "main.json").read_text(encoding="utf-8") == "[]"
    assert not (data / "config.toml").exists()
    assert not (data / "vault.key").exists()
    assert not (data / "README.txt").exists()
    assert not (tmp_path / "outside.txt").exists()


# ----------------------------------------------------------------------------- package


def test_package_install_bundles_the_whole_install(tmp_path: Path):
    root = tmp_path / "install"
    (root / "repo").mkdir(parents=True)
    (root / "repo" / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (root / "repo" / "nanomuse" / "x.py").mkdir(parents=True)
    (root / "repo" / "nanomuse" / "x.py" / "y.txt").write_text("hi", encoding="utf-8")
    (root / "repo" / ".git").mkdir()
    (root / "repo" / "x.pyc").write_text("c")
    (root / "data").mkdir()
    (root / "data" / "profile.json").write_text("{}", encoding="utf-8")
    (root / "data" / "threads").mkdir()
    (root / "data" / "threads" / "main.json").write_text("[]", encoding="utf-8")
    (root / "data" / "logs").mkdir()
    (root / "data" / "logs" / "x.log").write_text("log", encoding="utf-8")
    (root / "data" / "workspace").mkdir()
    (root / "data" / "workspace" / "stale.txt").write_text("stale", encoding="utf-8")
    (root / "workspace").mkdir()
    (root / "workspace" / "file.txt").write_text("ws", encoding="utf-8")
    (root / "config").mkdir()
    (root / "config" / "config.toml").write_text("data_dir = 'x'\n", encoding="utf-8")

    summary = package_install(root, tmp_path / "bundle.tar.gz")
    assert summary["files"] > 0
    assert (tmp_path / "bundle.tar.gz").is_file()

    names: set[str] = set()
    with tarfile.open(tmp_path / "bundle.tar.gz", "r:gz") as tar:
        names = {m.name for m in tar.getmembers()}
    assert "repo/pyproject.toml" in names
    assert "repo/nanomuse/x.py/y.txt" in names
    assert "data/profile.json" in names
    assert "data/threads/main.json" in names
    assert "workspace/file.txt" in names
    assert "config.toml" in names
    assert "manifest.json" in names
    assert not any(".git" in n for n in names)
    assert not any(n.endswith(".pyc") for n in names)
    assert not any("/logs/" in n or n.startswith("logs/") for n in names)
    assert not any("data/workspace" in n for n in names)


def test_package_install_refuses_a_folder_that_is_not_a_checkout(tmp_path: Path):
    with pytest.raises(ValueError):
        package_install(tmp_path, tmp_path / "x.tar.gz")


def test_cli_package_writes_a_bundle(tmp_path: Path):
    root = tmp_path / "install"
    (root / "repo").mkdir(parents=True)
    (root / "repo" / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (root / "data").mkdir()
    (root / "workspace").mkdir()
    out = tmp_path / "out.tar.gz"
    result = runner.invoke(app, ["phone", "package", "--root", str(root), "--out", str(out)])
    assert result.exit_code == 0, plain(result.output)
    assert out.is_file()


# ----------------------------------------------------------------------------- CLI


def test_cli_setup_writes_a_phone_configuration(tmp_path: Path):
    cfg = tmp_path / "input.toml"
    data = tmp_path / "data"
    cfg.write_text(f'data_dir = "{data.as_posix()}"\n', encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "phone",
            "setup",
            "--config",
            str(cfg),
            "--model",
            "qwen3:4b",
            "--no-service",
        ],
    )
    assert result.exit_code == 0, plain(result.output)
    written = (data / "config.toml").read_text(encoding="utf-8")
    assert written.startswith(MARKER)
    saved = json.loads((data / "app-settings.json").read_text(encoding="utf-8"))
    assert saved["cloud_mode"] == "local"
    assert saved["llm"]["model"] == "qwen3:4b"


def test_cli_setup_will_not_replace_a_hand_written_config_without_force(tmp_path: Path):
    cfg = tmp_path / "input.toml"
    data = tmp_path / "data"
    data.mkdir()
    cfg.write_text(f'data_dir = "{data.as_posix()}"\n', encoding="utf-8")
    hand_written = data / "config.toml"
    hand_written.write_text('data_dir = "/hand/written"\n', encoding="utf-8")
    refused = runner.invoke(
        app, ["phone", "setup", "--config", str(cfg), "--model", "qwen3:4b", "--no-service"]
    )
    assert refused.exit_code == 1
    assert hand_written.read_text(encoding="utf-8") == 'data_dir = "/hand/written"\n'
    forced = runner.invoke(
        app,
        [
            "phone",
            "setup",
            "--config",
            str(cfg),
            "--model",
            "qwen3:4b",
            "--no-service",
            "--force",
        ],
    )
    assert forced.exit_code == 0, plain(forced.output)
    assert hand_written.read_text(encoding="utf-8").startswith(MARKER)
    assert (data / "config.toml.before-phone").is_file()
