"""nanoMuse updating itself, in place, without a person at a terminal.

The flow ``POST /api/update/apply`` runs (or ``nanomuse update --apply``):

1. the newest release is found the way the *About* page finds it (the download mirror's
   index first, GitHub Releases second);
2. that release's ``nanomuse-<version>-py3-none-any.whl`` is downloaded from the release
   whose repository ``server.update_repo`` names (a fork releases under its own name);
3. the wheel is installed into the very environment this runtime runs in — the PC's venv,
   the phone capsule's ``/opt/nanomuse`` — with the previously installed wheel kept aside
   first, so ``--rollback`` (or ``POST /api/update/rollback``) can put it back;
4. the process re-executes itself in place: same interpreter, same arguments, same port —
   the WebView/PWA just reconnects. The loop's watchdog and the phone's RuntimeService see
   the same thing they always see after a restart.

A frozen build (the desktop's PyInstaller bundle) has no pip and no editable code: it
refuses here and keeps updating the way it already does, by downloading a new build.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tarfile
from collections.abc import Callable
from importlib import metadata
from pathlib import Path
from typing import Any

import httpx

from nanomuse import __version__
from nanomuse.logger import logger
from nanomuse.server.update import DEFAULT_REPO, TIMEOUT_S, newer_than

WHEEL_TIMEOUT_S = 300.0
RESTART_DELAY_S = 1.5  # let the HTTP answer leave before the process replaces itself

_RESTART_HOOK: Callable[[], None] | None = None


def set_restart_hook(hook: Callable[[], None] | None) -> None:
    """Register a callback invoked just before a self-update exits for restart (e.g. closing GUI window)."""
    global _RESTART_HOOK
    _RESTART_HOOK = hook


def _updates_dir() -> Path:
    root = Path(os.environ.get("NANOMUSE_DATA_DIR") or (Path.home() / ".nanomuse"))
    d = root / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _swap_error_path() -> Path:
    return _updates_dir() / "swap_error.txt"


def consume_swap_error() -> str:
    """Return and clear any swap error recorded by the previous update script."""
    err_path = _swap_error_path()
    if not err_path.is_file():
        return ""
    try:
        msg = err_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        msg = ""
    try:
        err_path.unlink(missing_ok=True)
    except OSError:
        pass
    return msg


def canonical_frozen_exe(exe: Path) -> Path:
    """Return the single non-versioned install binary path on desktop platforms.

    Keeps firewall permissions, taskbar pins, and Start Menu shortcuts pinned to one
    immutable path across updates instead of version-named binaries.
    """
    if os.environ.get("MINIMUSE_NO_INSTALL"):
        return exe
    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        canon = Path(local_app) / "Programs" / "MiniMuse" / "MiniMuse.exe"
        try:
            exe_norm = os.path.normcase(os.path.abspath(str(exe)))
            canon_norm = os.path.normcase(os.path.abspath(str(canon)))
        except OSError:
            return exe
        if exe_norm == canon_norm:
            return canon
        name_low = exe.name.lower()
        if canon.exists() or name_low != "minimuse.exe" or "win64" in name_low:
            try:
                canon.parent.mkdir(parents=True, exist_ok=True)
                return canon
            except OSError:
                return exe
        return exe
    if sys.platform.startswith("linux"):
        canon = Path.home() / ".local" / "bin" / "MiniMuse"
        try:
            if exe.resolve() == canon.resolve():
                return canon
        except OSError:
            pass
        if canon.exists() or ("linux" in exe.name.lower() and exe.name != "MiniMuse"):
            try:
                canon.parent.mkdir(parents=True, exist_ok=True)
                return canon
            except OSError:
                return exe
    return exe


def cleanup_updates_dir(*, keep_paths: set[Path] | None = None) -> int:
    """Remove stale downloaded update installers, wheels, and temp files so old versions are never kept."""
    d = _updates_dir()
    keep = {p.resolve() for p in (keep_paths or set()) if p.exists()}
    removed = 0
    for item in list(d.iterdir()):
        if item.name == "swap_error.txt":
            continue
        try:
            if item.resolve() in keep:
                continue
        except OSError:
            pass
        if item.is_dir() and item.name == "extract":
            shutil.rmtree(item, ignore_errors=True)
            removed += 1
            continue
        if item.is_file() and (
            item.name.endswith((".exe", ".tar.gz", ".tgz", ".whl", ".new", ".tmp", "-swap.cmd", "-swap.sh", ".apk"))
            or item.name.startswith("MiniMuse-")
        ):
            try:
                item.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    if frozen():
        exe = Path(sys.executable)
        canon = canonical_frozen_exe(exe)
        for base in {exe, canon}:
            for suffix in ("-swap.cmd", "-swap.sh", ".new", ".old", ".bak"):
                stale = base.with_name(base.name + suffix)
                try:
                    if stale.is_file() and stale.resolve() not in keep:
                        stale.unlink(missing_ok=True)
                        removed += 1
                except OSError:
                    pass
        # Also remove older versioned MiniMuse-*-win64.exe/zip downloads so old builds don't linger
        for dl_folder in {exe.parent, Path.home() / "Downloads"}:
            if not dl_folder.is_dir():
                continue
            for cand in list(dl_folder.glob("MiniMuse-*")):
                m = re.match(r"^MiniMuse-(\d+(?:\.\d+)+)-", cand.name, re.IGNORECASE)
                if not m:
                    continue
                ver = m.group(1)
                if newer_than(__version__, ver):
                    try:
                        if cand.resolve() != exe.resolve() and cand.resolve() not in keep:
                            cand.unlink(missing_ok=True)
                            removed += 1
                    except OSError:
                        pass
    return removed


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def is_editable_install() -> bool:
    """True when running directly from a local git/source checkout rather than an installed wheel/binary."""
    if frozen():
        return False
    try:
        import importlib.metadata as im

        dist = im.distribution("nanomuse")
        direct = dist.read_text("direct_url.json")
        if direct:
            info = json.loads(direct)
            if isinstance(info, dict) and info.get("dir_info", {}).get("editable"):
                return True
    except Exception:
        pass
    pkg_dir = Path(__file__).resolve().parents[2]
    return (pkg_dir / "pyproject.toml").exists() and (pkg_dir / "nanomuse" / "__init__.py").exists()


class SelfUpdate:
    """Find the newest release's wheel, install it under the running runtime, restart."""

    def __init__(
        self, update_check, repo: str = DEFAULT_REPO, *, current: str = __version__
    ) -> None:
        self.check = update_check  # the UpdateCheck instance /api/update already uses
        self.repo = repo.strip() or DEFAULT_REPO
        self.current = current
        self.busy = False
        self.last_error = ""
        self.last_auto_attempt = ""
        self._pending_swap: tuple[Path, Path] | None = None

    # ------------------------------------------------------------------ view
    def status(self) -> dict[str, Any]:
        d = _updates_dir()
        kept = d / "previous.whl"
        is_frozen = frozen()
        editable = is_editable_install()
        return {
            "current": self.current,
            "busy": self.busy,
            "frozen": is_frozen,
            "editable": editable,
            "auto_supported": True,
            "repo": self.repo,
            "previous_saved": kept.exists(),
            "last_error": self.last_error,
            "updates_dir": str(d),
        }

    # ------------------------------------------------------------------ apply
    async def apply(self) -> dict[str, Any]:
        if self.busy:
            return {"ok": False, "error": "an update is already running"}
        if frozen():
            return await self._apply_frozen()
        self.busy = True
        try:
            view = await self.check.view(force=True)
            latest = str(view.get("latest") or "")
            if not latest:
                return {
                    "ok": False,
                    "error": f"could not find a newer release ({view.get('error') or 'no answer'})",
                }
            if not newer_than(latest, self.current):
                return {
                    "ok": False,
                    "error": f"{self.current} is already the newest release",
                    "latest": latest,
                }

            wheel_url = await self._wheel_url(latest)
            if not wheel_url:
                return {
                    "ok": False,
                    "error": f"release {latest} carries no nanomuse wheel to install",
                    "latest": latest,
                }

            wheel = await self._download(wheel_url, latest)
            self._install(wheel)
            try:
                wheel.unlink(missing_ok=True)
                (_updates_dir() / "previous.whl").unlink(missing_ok=True)
            except OSError:
                pass
            installed = self._installed_version()
            if installed != latest:
                logger.warning("after install the metadata says {} (wanted {})", installed, latest)
            return {"ok": True, "from": self.current, "to": latest, "restarting": True}
        finally:
            self.busy = False

    async def _apply_frozen(self) -> dict[str, Any]:
        """A one-file or bundled desktop build: download the newer build from the release,
        verify its SHA-256 if published, queue swap over this binary, and restart in place."""
        self.busy = True
        try:
            view = await self.check.view(force=True)
            latest = str(view.get("latest") or "")
            if not newer_than(latest, self.current):
                return {
                    "ok": False,
                    "error": f"{self.current} is already the newest release",
                    "latest": latest or None,
                }

            exe = Path(sys.executable)
            target_exe = canonical_frozen_exe(exe)
            candidates = _frozen_asset_candidates(latest)
            asset_name, url, sums_url = await self._find_frozen_asset(latest, candidates)
            if not url or not asset_name:
                want = candidates[0] if candidates else f"MiniMuse-{latest}"
                return {
                    "ok": False,
                    "error": f"release {latest} has no matching asset (looked for {want})",
                    "latest": latest,
                }

            dl_dir = _updates_dir()
            dl_path = dl_dir / asset_name
            await self._download_to(url, dl_path)
            if sums_url:
                await self._verify_sha256(dl_path, asset_name, sums_url)

            new_bin = target_exe.with_name(target_exe.name + ".new")
            if asset_name.endswith((".tar.gz", ".tgz")):
                extracted = _extract_binary_from_tarball(dl_path, dl_dir / "extract", target_exe.name)
                shutil.copyfile(extracted, new_bin)
            else:
                shutil.copyfile(dl_path, new_bin)
            if os.name == "nt":
                with contextlib.suppress(OSError):
                    os.remove(f"{new_bin}:Zone.Identifier")
            else:
                with contextlib.suppress(OSError):
                    new_bin.chmod(0o755)
            # Remove the versioned archive/installer right away so updates stay in-place on disk.
            try:
                dl_path.unlink(missing_ok=True)
            except OSError:
                pass
            shutil.rmtree(dl_dir / "extract", ignore_errors=True)
            cleanup_updates_dir(keep_paths={new_bin})
            self._pending_swap = (target_exe, new_bin)
            return {"ok": True, "from": self.current, "to": latest, "restarting": True}
        finally:
            self.busy = False

    def restart_in_place(self) -> None:
        if self._pending_swap is not None:
            exe, new = self._pending_swap
            self._pending_swap = None
            schedule_frozen_swap(exe, new)
            return
        restart_in_place()

    async def rollback(self) -> dict[str, Any]:
        if frozen():
            return {
                "ok": False,
                "error": "this is a frozen build; roll it back by reinstalling the old build",
            }
        kept = _updates_dir() / "previous.whl"
        if not kept.exists():
            return {"ok": False, "error": "no previous wheel was kept"}
        self.busy = True
        try:
            self._install(kept)
            return {"ok": True, "to": self._installed_version(), "restarting": True}
        finally:
            self.busy = False

    async def _release_assets(self, version: str) -> list[dict[str, Any]]:
        """Fetch the assets list for release ``version`` (trying tag lookup first, then list)."""
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"MiniMuse/{self.current}",
        }
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                for tag in (f"v{version}", version):
                    r = await client.get(
                        f"https://api.github.com/repos/{self.repo}/releases/tags/{tag}",
                        headers=headers,
                        timeout=TIMEOUT_S,
                    )
                    if r.status_code == 200:
                        data = r.json()
                        if isinstance(data, dict) and isinstance(data.get("assets"), list):
                            return list(data["assets"])
                r = await client.get(
                    f"https://api.github.com/repos/{self.repo}/releases?per_page=25",
                    headers=headers,
                    timeout=TIMEOUT_S,
                )
                if r.status_code == 200:
                    for rel in r.json():
                        if str(rel.get("tag_name") or "").lstrip("v") == version:
                            return list(rel.get("assets") or [])
        except Exception as exc:
            logger.debug("_release_assets API lookup failed ({}), using direct URL fallback", exc)
        return []

    async def _direct_asset_exists(self, url: str) -> bool:
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                r = await client.head(
                    url,
                    headers={"User-Agent": f"MiniMuse/{self.current}"},
                    timeout=TIMEOUT_S,
                )
                return r.status_code == 200
        except Exception:
            return False

    async def _wheel_url(self, version: str) -> str:
        """The release ``v<version>`` of ``server.update_repo``'s wheel asset."""
        suffix = re.escape(version)
        for asset in await self._release_assets(version):
            name = str(asset.get("name") or "")
            if (
                re.fullmatch(rf"nanomuse-{suffix}.*-py3-none-any\.whl", name)
                or re.fullmatch(rf"minimuse-{suffix}.*-py3-none-any\.whl", name)
                or name == f"nanomuse-{version}-py3-none-any.whl"
            ):
                return str(asset.get("browser_download_url") or "")
        # Direct GitHub release download URL fallback (immune to api.github.com rate limits)
        direct = f"https://github.com/{self.repo}/releases/download/v{version}/nanomuse-{version}-py3-none-any.whl"
        if await self._direct_asset_exists(direct):
            return direct
        return ""

    async def _find_frozen_asset(self, version: str, candidates: list[str]) -> tuple[str, str, str]:
        """Return ``(asset_name, download_url, sha256sums_url)`` for ``version``."""
        assets = await self._release_assets(version)
        by_name = {
            str(a.get("name") or ""): str(a.get("browser_download_url") or "") for a in assets
        }
        sums_url = (
            by_name.get(f"MiniMuse-{version}-SHA256SUMS")
            or by_name.get("SHA256SUMS")
            or by_name.get("SHA256SUMS.txt")
            or ""
        )
        for cand in candidates:
            if cand in by_name and by_name[cand]:
                return cand, by_name[cand], sums_url
        # Direct GitHub release download URL fallback when api.github.com is rate-limited
        base_dl = f"https://github.com/{self.repo}/releases/download/v{version}"
        if not sums_url:
            direct_sums = f"{base_dl}/MiniMuse-{version}-SHA256SUMS"
            if await self._direct_asset_exists(direct_sums):
                sums_url = direct_sums
        for cand in candidates:
            direct_url = f"{base_dl}/{cand}"
            if await self._direct_asset_exists(direct_url):
                return cand, direct_url, sums_url
        return "", "", sums_url

    async def _asset_url(self, version: str, asset_name: str) -> str:
        """A named asset of release ``v<version>``."""
        for asset in await self._release_assets(version):
            if str(asset.get("name") or "") == asset_name:
                return str(asset.get("browser_download_url") or "")
        return ""

    async def _download(self, url: str, version: str) -> Path:
        dest = _updates_dir() / f"nanomuse-{version}-py3-none-any.whl"
        await self._download_to(url, dest)
        return dest

    async def _download_to(self, url: str, dest: Path) -> None:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            async with client.stream(
                "GET",
                url,
                headers={"User-Agent": f"MiniMuse/{self.current}"},
                timeout=WHEEL_TIMEOUT_S,
            ) as r:
                r.raise_for_status()
                tmp = dest.with_suffix(dest.suffix + ".part")
                with open(tmp, "wb") as f:
                    async for chunk in r.aiter_bytes():
                        f.write(chunk)
                tmp.replace(dest)

    async def _verify_sha256(self, file_path: Path, asset_name: str, sums_url: str) -> None:
        """Verify downloaded file digest when its name appears in the release checksum file."""
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                r = await client.get(
                    sums_url, headers={"User-Agent": f"MiniMuse/{self.current}"}, timeout=TIMEOUT_S
                )
                if r.status_code != 200:
                    return
                expected = ""
                for line in r.text.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2 and Path(parts[-1].lstrip("*")).name == asset_name:
                        expected = parts[0].lower()
                        break
                if not expected:
                    return
        except Exception as exc:  # noqa: BLE001 — best-effort checksum fetch
            logger.debug("sha256 check skipped ({}): {}", asset_name, exc)
            return
        digest = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                digest.update(chunk)
        actual = digest.hexdigest().lower()
        if actual != expected:
            file_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"checksum mismatch for {asset_name}: expected {expected[:12]}, got {actual[:12]}"
            )

    def _keep_previous(self) -> None:
        """The wheel of what runs now, kept for --rollback."""
        try:
            dist = next(
                (
                    p
                    for p in Path(sysconfig.get_paths()["purelib"]).glob(
                        f"nanomuse-{re.escape(self.current)}-*.whl"
                    )
                ),
                None,
            )
            if dist is None:
                return  # an editable / dev checkout has no wheel to keep; nothing to roll back to
            shutil.copy2(dist, _updates_dir() / "previous.whl")
        except Exception as exc:  # noqa: BLE001 — keeping the old wheel is best-effort
            logger.debug("could not keep the previous wheel: {}", exc)

    def _install(self, wheel: Path) -> None:
        import subprocess
        import zipfile

        from nanomuse.win_proc import hidden_subprocess_kwargs

        editable = is_editable_install()
        pkg_root = Path(__file__).resolve().parents[2]
        if editable and (pkg_root / ".git").exists():
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["git", "-C", str(pkg_root), "fetch", "origin", "--tags", "--quiet"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                    **hidden_subprocess_kwargs(),
                )
                subprocess.run(
                    ["git", "-C", str(pkg_root), "pull", "--ff-only", "--quiet"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                    **hidden_subprocess_kwargs(),
                )

        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--no-deps",
            "--quiet",
        ]
        purelib = sysconfig.get_paths().get("purelib", "")
        in_venv = sys.prefix != sys.base_prefix
        if not in_venv and purelib and not os.access(purelib, os.W_OK):
            cmd.append("--user")
        cmd.append(str(wheel))
        done = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600, **hidden_subprocess_kwargs()
        )
        pip_ok = done.returncode == 0
        if not pip_ok or editable:
            try:
                with zipfile.ZipFile(wheel, "r") as zf:
                    if purelib and os.access(purelib, os.W_OK):
                        purelib_path = Path(purelib)
                        for old_info in purelib_path.glob("nanomuse-*.dist-info"):
                            shutil.rmtree(old_info, ignore_errors=True)
                        zf.extractall(purelib_path)
                        pip_ok = True
                    if editable and (pkg_root / "nanomuse").is_dir() and os.access(pkg_root, os.W_OK):
                        for member in zf.infolist():
                            if member.filename.startswith("nanomuse/"):
                                zf.extract(member, pkg_root)
                        pip_ok = True
            except Exception as exc:
                if not pip_ok:
                    raise RuntimeError(
                        f"wheel install failed ({exc}); pip said: {(done.stderr or done.stdout).strip()[:300]}"
                    ) from exc
        if not pip_ok:
            raise RuntimeError(f"pip install failed: {(done.stderr or done.stdout).strip()[:400]}")

    def _installed_version(self) -> str:
        try:
            if is_editable_install():
                init_py = Path(__file__).resolve().parents[1] / "__init__.py"
                if init_py.is_file():
                    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init_py.read_text(encoding="utf-8"))
                    if m:
                        return m.group(1)
            return metadata.version("nanomuse")
        except Exception:  # noqa: BLE001
            return ""


def _frozen_asset_candidates(version: str) -> list[str]:
    arch = platform.machine().lower()
    is_arm64 = arch in ("arm64", "aarch64")
    if os.name == "nt":
        return [
            f"MiniMuse-{version}-win64.exe",
            "MiniMuse-win64.exe",
        ]
    if sys.platform == "darwin":
        tag_arch = "arm64" if is_arm64 else "x64"
        return [
            f"MiniMuse-{version}-mac-{tag_arch}.tar.gz",
            f"MiniMuse-{version}-macos-{tag_arch}.tar.gz",
            f"MiniMuse-{version}-mac-{tag_arch}",
            f"MiniMuse-{version}-macos-{tag_arch}",
        ]
    tag_arch = "arm64" if is_arm64 else "x64"
    return [
        f"MiniMuse-{version}-linux",
        f"MiniMuse-{version}-linux-{tag_arch}",
        f"MiniMuse-{version}-linux-{tag_arch}.tar.gz",
        "MiniMuse-linux",
    ]


def _extract_binary_from_tarball(archive: Path, dest_dir: Path, exe_name: str) -> Path:
    if dest_dir.exists():
        shutil.rmtree(dest_dir, ignore_errors=True)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:*") as tf:
        tf.extractall(dest_dir, filter="data")
    candidates = [
        p
        for p in dest_dir.rglob("*")
        if p.is_file() and (p.name == exe_name or p.name.lower().startswith("minimuse"))
    ]
    if not candidates:
        candidates = [p for p in dest_dir.rglob("*") if p.is_file() and os.access(p, os.X_OK)]
    if not candidates:
        raise RuntimeError(f"updated archive {archive.name} contains no MiniMuse executable")
    return max(candidates, key=lambda p: p.stat().st_size)


def schedule_frozen_swap(exe: Path, new: Path) -> None:
    """Schedule ``swap_and_restart`` after the current HTTP response has had time to flush."""
    try:
        loop = asyncio.get_running_loop()
        loop.call_later(0.4, lambda: swap_and_restart(exe, new))
        return
    except RuntimeError:
        pass
    swap_and_restart(exe, new)


def swap_and_restart(exe: Path, new: Path) -> None:
    """Replace a running one-file build with the downloaded one and relaunch in the same mode.

    When launched in GUI app mode (no arguments), relaunch without ``serve`` so the native
    desktop window reopens cleanly. When launched from CLI (e.g. ``serve``), preserve args.
    """
    extra_args = [a for a in sys.argv[1:] if a]
    err_file = _swap_error_path()
    try:
        err_file.unlink(missing_ok=True)
    except OSError:
        pass
    pid = os.getpid()
    if os.name == "nt":
        # Perform atomic NTFS in-place swap in Python first (renaming a running .exe to .exe.old
        # succeeds on NTFS while the process is still alive, avoiding locked-file retry loops).
        old = exe.with_name(exe.name + ".old")
        swapped_inline = False
        if frozen() and exe.exists() and new.exists():
            try:
                old.unlink(missing_ok=True)
                exe.rename(old)
                new.rename(exe)
                swapped_inline = True
            except OSError:
                if not exe.exists() and old.exists():
                    try:
                        old.rename(exe)
                    except OSError:
                        pass
        cmdline = subprocess.list2cmdline(extra_args) if extra_args else ""
        launch = f'start "" "{exe}" {cmdline}'.strip()
        bat = exe.with_name(exe.name + "-swap.cmd")
        bat.write_text(
            "@echo off\r\n"
            f'set "SWAPPED={"1" if swapped_inline else "0"}"\r\n'
            'if "%SWAPPED%"=="1" goto :launch\r\n'
            f'if not exist "{new}" (\r\n'
            '  set "SWAPPED=1"\r\n'
            "  goto :launch\r\n"
            ")\r\n"
            "for /L %%i in (1,1,45) do (\r\n"
            "  timeout /t 1 /nobreak >nul 2>&1\r\n"
            f"  if %%i==8 taskkill /F /PID {pid} >nul 2>&1\r\n"
            f'  move /y "{exe}" "{old}" >nul 2>&1\r\n'
            f'  move /y "{new}" "{exe}" >nul 2>&1 && (\r\n'
            '    set "SWAPPED=1"\r\n'
            "    goto :launch\r\n"
            "  )\r\n"
            ")\r\n"
            'if "%SWAPPED%"=="0" (\r\n'
            f'  echo Could not replace {exe} because it stayed locked by another process.> "{err_file}"\r\n'
            ")\r\n"
            ":launch\r\n"
            f"{launch}\r\n"
            'del "%~f0"\r\n',
            encoding="ascii",
        )
        from nanomuse.win_proc import hidden_subprocess_kwargs

        # Never combine CREATE_NO_WINDOW (0x08000000) with DETACHED_PROCESS (0x00000008):
        # MSDN documents them as mutually exclusive, and combining them causes console child
        # processes (timeout/ping/taskkill) to crash with 0xc0000142 (STATUS_DLL_INIT_FAILED).
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        env = dict(os.environ)
        env["MINIMUSE_CANONICAL_RELAUNCH"] = "1"
        subprocess.Popen(
            ["cmd", "/c", str(bat)],
            close_fds=True,
            env=env,
            **hidden_subprocess_kwargs(hide_window=False, creationflags=flags),
        )
    else:
        quoted = " ".join(shlex.quote(a) for a in extra_args)
        launch = (
            f'nohup "{exe}" {quoted} >/dev/null 2>&1 &'
            if quoted
            else f'nohup "{exe}" >/dev/null 2>&1 &'
        )
        sh = exe.with_name(exe.name + "-swap.sh")
        sh.write_text(
            '#!/bin/sh\nswap_ok=0\nfor i in $(seq 1 30); do\n'
            f'  sleep 1\n  mv -f "{new}" "{exe}" 2>/dev/null && {{ swap_ok=1; break; }}\ndone\n'
            f'if [ "$swap_ok" -eq 0 ]; then\n'
            f'  printf "Could not replace %s during self-update.\\n" "{exe}" > "{err_file}"\n'
            "fi\n"
            f'chmod +x "{exe}"\nMINIMUSE_CANONICAL_RELAUNCH=1 {launch}\nrm -f "$0"\n',
            encoding="utf-8",
        )
        sh.chmod(0o755)
        subprocess.Popen(["/bin/sh", str(sh)], start_new_session=True, close_fds=True)
    if _RESTART_HOOK is not None:
        try:
            _RESTART_HOOK()
        except Exception:  # noqa: BLE001
            pass
    import _thread

    _thread.interrupt_main()
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        import threading

        threading.Timer(0.6, lambda: os._exit(0)).start()


def restart_in_place() -> None:
    """Re-exec this very process: same interpreter, same arguments, same port.

    The phone's runtime service and the PC's watchdog both treat it as the ordinary
    restart they already handle; the web app reconnects on its own.
    """
    argv = [sys.executable, "-m", "nanomuse", *sys.argv[1:]]
    logger.info("updating in place: {}", " ".join(argv))

    def _do_restart() -> None:
        if _RESTART_HOOK is not None:
            try:
                _RESTART_HOOK()
            except Exception:
                pass
        if os.name == "nt":
            from nanomuse.win_proc import hidden_subprocess_kwargs

            cmdline = subprocess.list2cmdline(argv)
            flags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
            subprocess.Popen(
                ["cmd", "/c", f"ping 127.0.0.1 -n 3 >nul && {cmdline}"],
                close_fds=True,
                **hidden_subprocess_kwargs(creationflags=flags),
            )
            os._exit(0)
        os.execv(sys.executable, argv)

    # give in-flight writes and HTTP answer a beat, then hand the port/process to the new code
    try:
        asyncio.get_running_loop().call_later(0.4, _do_restart)
    except RuntimeError:
        _do_restart()
