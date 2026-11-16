"""The app's lock screen: a PIN the person sets, so the page on a shared device (or a
phone someone else picks up) asks before it shows the conversation.

The lock is **per device**, not per runtime.  The phone that sets a PIN or a fingerprint
does not lock the computer, and the other way round: each browser identifies itself with
a ``nanomuse_device_id`` kept in its own storage, and the lock data lives under that id
in the app settings.  A fingerprint or face is registered on one device's authenticator
and belongs to that device alone.

The PIN is stored only as a PBKDF2 hash; the browser never learns it, and verification
goes through ``/api/lock/verify``, which counts failed tries in memory and pauses after
several, so guessing over the LAN is slow.

Migration from the pre-device shape: a runtime with one global lock entry keeps it until
the device that owns it shows up.  A device whose local credential id matches the stored
one claims the whole entry (the PIN and the credentials move under that device's id); a
PIN-only entry stays shared, because a PIN is something the person knows on every device,
not something one device owns.  The moment a device sets or changes anything, its own
entry exists and the shared one no longer applies to it.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from pathlib import Path
from typing import Any

from nanomuse.config import load_app_settings, save_app_settings

DATA_KEY = "app_lock"
_ITERATIONS = 120_000
# failed tries before the verify route goes quiet for a while
_MAX_TRIES = 8
_LOCKOUT_S = 300.0
# How long an unlock lasts, in seconds. 0 asks on every fresh open (an app that was
# fully closed); the rest are the picker's steps, 30 seconds to 24 hours.
_VALID_TIMEOUTS = (0, 30, 60, 180, 300, 900, 3600, 86400)
# the old minute values, read only to migrate a stored choice
_LEGACY_MINUTES = (0, 15, 60, 1440)


def _migrate_timeout(data: dict[str, Any]) -> int:
    """The stored timeout in seconds, with the older minutes values read across."""
    raw = data.get("timeout_seconds")
    if isinstance(raw, int) and raw in _VALID_TIMEOUTS:
        return raw
    legacy = data.get("timeout_minutes")
    if isinstance(legacy, int) and legacy in _LEGACY_MINUTES and legacy * 60 in _VALID_TIMEOUTS:
        return legacy * 60
    # nothing stored yet: the old default of one hour
    return 3600


def _hash(pin: str, salt: bytes, iterations: int = _ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations)


def _resolve_credentials(data: dict[str, Any]) -> tuple[str, str]:
    """Return (biometric_credential_id, passkey_credential_id) only when explicitly configured."""
    bio_id = str(data.get("biometric_credential_id") or "").strip()
    passkey_id = str(data.get("passkey_credential_id") or "").strip()
    return bio_id, passkey_id


def _enabled_of(data: dict[str, Any]) -> bool:
    pin = bool(data.get("pin_hash"))
    bio, passkey = _resolve_credentials(data)
    return not bool(data.get("disabled")) and (pin or bool(bio) or bool(passkey))


class AppLockStore:
    """The lock behind the lock screen, per device: a PIN, or a device biometric/passkey."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._failed: list[float] = []

    # ------------------------------------------------------------------ storage
    def _raw(self) -> dict[str, Any]:
        data = load_app_settings(self.data_dir).get(DATA_KEY)
        return data if isinstance(data, dict) else {}

    def _container(self) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        """(devices, legacy): the per-device entries and the pre-device entry, if any.

        The legacy entry is the old one-device shape (pin_hash and friends straight under
        ``app_lock``); it is kept until its owner claims it or someone writes their own."""
        raw = self._raw()
        devices = raw.get("devices")
        if isinstance(devices, dict):
            legacy = raw.get("legacy")
            legacy = dict(legacy) if isinstance(legacy, dict) else {}
            return {k: dict(v) for k, v in devices.items() if isinstance(v, dict)}, legacy
        return {}, {k: v for k, v in raw.items() if k != "devices"}

    def _save(self, devices: dict[str, dict[str, Any]], legacy: dict[str, Any]) -> None:
        stored = load_app_settings(self.data_dir)
        payload: dict[str, Any] = {"devices": devices}
        if legacy:
            payload["legacy"] = legacy
        stored[DATA_KEY] = payload
        save_app_settings(self.data_dir, stored)

    def _effective(self, device_id: str, local_creds: tuple[str, ...] = ()) -> dict[str, Any]:
        """The lock entry that applies to this device, claiming the legacy one when this
        device carries the credential the legacy entry names."""
        devices, legacy = self._container()
        if device_id and device_id in devices:
            return devices[device_id]
        if not legacy or not device_id:
            return {}
        stored_ids = {
            str(legacy.get("biometric_credential_id") or ""),
            str(legacy.get("passkey_credential_id") or ""),
        }
        stored_ids.discard("")
        if stored_ids:
            if stored_ids & {str(c).strip() for c in local_creds}:
                devices[device_id] = legacy
                self._save(devices, {})
                return legacy
            return {}
        # a PIN-only legacy entry is shared: a PIN is knowledge, not a device
        return legacy

    def _write(self, device_id: str, data: dict[str, Any]) -> None:
        devices, legacy = self._container()
        devices[device_id] = data
        self._save(devices, legacy)

    def _view_of(self, data: dict[str, Any]) -> dict[str, Any]:
        pin_enabled = bool(data.get("pin_hash"))
        bio_id, passkey_id = _resolve_credentials(data)
        return {
            "enabled": _enabled_of(data),
            "biometric": bool(bio_id),
            "passkey": bool(passkey_id),
            "pin_enabled": pin_enabled,
            "biometric_credential_id": bio_id or None,
            "passkey_credential_id": passkey_id or None,
            "timeout_seconds": _migrate_timeout(data),
            "lock_on_leave": bool(data.get("lock_on_leave")),
        }

    def view(self, device_id: str = "", local_creds: tuple[str, ...] = ()) -> dict[str, Any]:
        """What this device's lock screen needs before asking anything."""
        return self._view_of(self._effective(device_id, local_creds))

    def summary(self) -> dict[str, Any]:
        """One line for the settings home: whether any device has the lock on, and how."""
        devices, legacy = self._container()
        entries = list(devices.values()) + ([legacy] if legacy else [])
        if not entries:
            return {"enabled": False, "biometric": False, "passkey": False, "pin_enabled": False}
        return {
            "enabled": any(_enabled_of(e) for e in entries),
            "biometric": any(e.get("biometric_credential_id") for e in entries),
            "passkey": any(e.get("passkey_credential_id") for e in entries),
            "pin_enabled": any(e.get("pin_hash") for e in entries),
            "timeout_seconds": _migrate_timeout(entries[0]),
            "lock_on_leave": bool(entries[0].get("lock_on_leave")),
        }

    def set_pin(
        self,
        device_id: str,
        pin: str,
        *,
        keep_biometric: bool = False,
        timeout_seconds: int | None = None,
        lock_on_leave: bool | None = None,
    ) -> dict[str, Any]:
        """Set or change this device's PIN; an empty one turns the PIN off (keeping
        biometric/passkey if requested, or turning this device's lock off)."""
        pin = str(pin).strip()
        current = self._effective(device_id)
        bio_id, passkey_id = _resolve_credentials(current)
        next_timeout = (
            timeout_seconds if timeout_seconds in _VALID_TIMEOUTS else _migrate_timeout(current)
        )
        next_leave = (
            lock_on_leave if lock_on_leave is not None else bool(current.get("lock_on_leave"))
        )
        if not pin:
            if keep_biometric and (bio_id or passkey_id):
                next_data: dict[str, Any] = {
                    "timeout_seconds": next_timeout,
                    "lock_on_leave": next_leave,
                }
                if bio_id:
                    next_data["biometric_credential_id"] = bio_id
                if passkey_id:
                    next_data["passkey_credential_id"] = passkey_id
            else:
                next_data = {"timeout_seconds": next_timeout, "lock_on_leave": next_leave}
        else:
            salt = secrets.token_bytes(16)
            next_data = {
                "pin_hash": _hash(pin, salt).hex(),
                "salt": salt.hex(),
                "iterations": _ITERATIONS,
                "timeout_seconds": next_timeout,
                "lock_on_leave": next_leave,
            }
            if bio_id:
                next_data["biometric_credential_id"] = bio_id
            if passkey_id:
                next_data["passkey_credential_id"] = passkey_id
        self._write(device_id, next_data)
        return self._view_of(next_data)

    def set_timeout(
        self, device_id: str, timeout_seconds: int, lock_on_leave: bool | None = None
    ) -> dict[str, Any]:
        """Update how long an unlock lasts on this device, and whether switching away locks it."""
        data = dict(self._effective(device_id))
        data["timeout_seconds"] = timeout_seconds if timeout_seconds in _VALID_TIMEOUTS else 3600
        data["lock_on_leave"] = (
            lock_on_leave if lock_on_leave is not None else bool(data.get("lock_on_leave"))
        )
        self._write(device_id, data)
        return self._view_of(data)

    def verify(self, device_id: str, pin: str) -> bool:
        """Constant-time comparison, with a pause after a run of wrong tries."""
        data = self._effective(device_id)
        bio_id, passkey_id = _resolve_credentials(data)
        if not data.get("pin_hash"):
            # No PIN on this device. A device with a biometric/passkey proves presence
            # through WebAuthn before the lock opens; with nothing registered the lock
            # is simply off for this device.
            return not bool(bio_id or passkey_id)
        now = time.monotonic()
        self._failed = [t for t in self._failed if now - t < _LOCKOUT_S]
        if len(self._failed) >= _MAX_TRIES:
            return False
        salt = bytes.fromhex(str(data.get("salt") or ""))
        iterations = int(data.get("iterations") or _ITERATIONS)
        ok = hmac.compare_digest(_hash(str(pin), salt, iterations).hex(), str(data["pin_hash"]))
        if ok:
            self._failed.clear()
        else:
            self._failed.append(now)
        return ok

    # ------------------------------------------------------------------ biometric & passkey
    def register_biometric(
        self, device_id: str, credential_id: str, kind: str = "biometric"
    ) -> dict[str, Any]:
        data = dict(self._effective(device_id))
        bio_id, passkey_id = _resolve_credentials(data)
        data.pop("webauthn_credential_id", None)
        if bio_id:
            data["biometric_credential_id"] = bio_id
        if passkey_id:
            data["passkey_credential_id"] = passkey_id

        key = "passkey_credential_id" if kind == "passkey" else "biometric_credential_id"
        cid = str(credential_id).strip()[:300]
        if cid:
            data[key] = cid
            data.pop("disabled", None)
        else:
            data.pop(key, None)
        self._write(device_id, data)
        return self._view_of(data)

    def biometric_credential(self, device_id: str = "", kind: str = "") -> str | None:
        data = self._effective(device_id)
        bio_id, passkey_id = _resolve_credentials(data)
        if kind == "passkey":
            return passkey_id or None
        if kind == "biometric":
            return bio_id or None
        return bio_id or passkey_id or None

    # ------------------------------------------------------------------ reset
    RESET_TTL_S = 1800.0

    def request_reset(self, device_id: str) -> dict[str, Any]:
        """Begin a password reset for this device: one 8-character code, good for 30
        minutes, one use.  The code leaves the server only through the delivery channels
        the service sends it on (e-mail, the terminal); nothing stores it in the clear."""
        import random
        import string as _string

        code = "".join(
            random.SystemRandom().choice(_string.ascii_uppercase + _string.digits) for _ in range(8)
        )
        stored = load_app_settings(self.data_dir)
        stored["app_lock_reset"] = {
            "hash": hashlib.sha256(code.encode()).hexdigest(),
            "expires": time.time() + self.RESET_TTL_S,
            "device_id": device_id,
        }
        save_app_settings(self.data_dir, stored)
        return {"code": code, "expires_in": int(self.RESET_TTL_S)}

    def _consume_reset_code(self, device_id: str, code: str) -> None:
        stored = load_app_settings(self.data_dir)
        reset = stored.get("app_lock_reset")
        ok = False
        if isinstance(reset, dict):
            expires = float(reset.get("expires") or 0)
            cleaned = str(code or "").strip().upper()
            digest = hashlib.sha256(cleaned.encode()).hexdigest()
            if (
                time.time() <= expires
                and hmac.compare_digest(digest, str(reset.get("hash") or ""))
                and str(reset.get("device_id") or "") == device_id
            ):
                ok = True
        if not ok:
            raise ValueError("That reset code is wrong or expired. Ask for a new one.")
        stored.pop("app_lock_reset", None)
        save_app_settings(self.data_dir, stored)
        try:
            (self.data_dir / "lock-reset-code.txt").unlink(missing_ok=True)
        except OSError:
            pass

    def reset_with_code(self, device_id: str, code: str, new_pin: str) -> dict[str, Any]:
        """The code resets this device's lock: it is consumed, and the new password/PIN
        takes over (the device's fingerprint/passkey stays)."""
        pin = str(new_pin).strip()
        if len(pin) < 4:
            raise ValueError("Use at least 4 characters.")
        self._consume_reset_code(device_id, code)
        return self.set_pin(device_id, pin, keep_biometric=True)

    def disable_with_code(self, device_id: str, code: str) -> dict[str, Any]:
        """Consume the reset code and turn App Lock completely off for this device."""
        self._consume_reset_code(device_id, code)
        current = self._effective(device_id)
        next_data: dict[str, Any] = {
            "timeout_seconds": _migrate_timeout(current),
            "lock_on_leave": bool(current.get("lock_on_leave")),
        }
        devices, legacy = self._container()
        if device_id:
            devices[device_id] = next_data
        # Clear any stale biometric-only legacy lock so fresh/other devices aren't haunted by it.
        if legacy and not legacy.get("pin_hash"):
            legacy = {}
        self._save(devices, legacy)
        return self._view_of(next_data)
