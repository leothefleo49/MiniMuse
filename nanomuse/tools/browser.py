"""Browser tool. The page is summarised for the model as readable text plus a numbered list of
interactive elements; actions refer to those numbers.

Where the page renders is a :class:`~nanomuse.tools.browser_backends.BrowserBackend`: a
Playwright Chromium on the computer (``pip install "nanomuse[browser]" && playwright install
chromium``), with a profile kept under the workspace so logins survive a restart; or the
nanoMuse app's own WebView on the phone, signed in to whatever the user is signed in to.
``backend="auto"`` takes Playwright when it is installed and the phone otherwise.

What the browser shows is also shown to the user: after every action a JPEG frame of the
viewport goes to ``on_frame`` (the app turns that into a live browser card), and the user can
take over — click, type, open a URL — through :meth:`Browser.user_action`, or, on the phone,
by pulling the very same WebView into view. That is how a login happens: the agent stops at
the form, the user signs in, the agent carries on with the page as the user left it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from pydantic import PrivateAttr

from nanomuse.agent.holds import HAND_OVER_TIMEOUT_S, took_over_note
from nanomuse.logger import logger
from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment
from nanomuse.tools.browser_backends import (
    PROFILES,
    BrowserBackend,
    BrowserProfile,
    ChromeBridgeBackend,
    DeviceBackend,
    PlaywrightBackend,
    profile_named,
)
from nanomuse.tools.web import _is_private_host, host_of

# the default (desktop) viewport; the frame the user sees is whatever the backend's profile is
VIEWPORT = PROFILES["desktop"].viewport

# what a fetch hands back to the model at most
FETCH_MAX_HOPS = 5
FETCH_MAX_CHARS = 20_000


def _redirect_target(result: dict[str, Any], base: str) -> str | None:
    """Where a redirect answer points (absolute), or ``None`` for a final answer."""
    status = int(result.get("status") or 0)
    if status not in (301, 302, 303, 307, 308):
        return None
    headers = {str(k).lower(): str(v) for k, v in (result.get("headers") or {}).items()}
    location = headers.get("location", "").strip()
    if not location:
        return None
    return urljoin(str(result.get("url") or base), location)


@dataclass
class BrowserFrame:
    """One picture of the browser, right after something happened."""

    url: str
    title: str
    # a caption for people: "Opened example.com", "Clicked 'Sign in'", "You typed"
    action: str
    jpeg: bytes
    by_user: bool = False
    # the chat this belongs to when known (user actions come from the app, not an agent run)
    thread: str | None = None
    # "playwright" or "device": the app offers the phone's own take-over for the second
    backend: str = ""
    width: int = 0
    height: int = 0


_ANNOTATE_JS = """
(maxElements) => {
  const sel = 'a[href], button, input, textarea, select, summary, [role=button], [role=link], [role=tab], [role=menuitem], [onclick], [contenteditable=true]';
  document.querySelectorAll('[data-om-idx]').forEach(e => e.removeAttribute('data-om-idx'));
  const visible = (e) => {
    const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const els = Array.from(document.querySelectorAll(sel)).filter(visible).slice(0, maxElements);
  return els.map((e, i) => {
    e.setAttribute('data-om-idx', String(i));
    const text = (e.innerText || e.value || e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.getAttribute('title') || e.getAttribute('name') || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
    return { i, tag: e.tagName.toLowerCase(), type: e.getAttribute('type') || '', text, href: (e.getAttribute('href') || '').slice(0, 120) };
  });
}
"""

_TEXT_JS = "() => document.body ? document.body.innerText : ''"

_LABEL_JS = """
(i) => {
  const e = document.querySelector('[data-om-idx="' + i + '"]');
  return e ? (e.innerText || e.value || e.getAttribute('aria-label') || e.getAttribute('placeholder') || '').trim() : '';
}
"""


def playwright_available() -> bool:
    return PlaywrightBackend.available()


class Browser(BaseTool):
    name: str = "browser"
    description: str = (
        "Control a real web browser to complete tasks on websites (search, read, fill forms, click, sign in). "
        "Actions: `navigate` (url), `extract` (read current page + numbered interactive elements), "
        "`click` (index), `type` (index, text, submit=true to press Enter; supports `{{vault:LOGIN_<ID>}}` placeholders), "
        "`login` (site: auto-fill username & password from the saved password manager for the current page or `site`, "
        "optionally specifying `username_index`/`password_index` and `submit=true`), "
        "`press` (key, e.g. 'Enter'), `scroll` (direction up|down), `back`, `wait` (seconds, for a page that is still drawing itself), "
        "`screenshot`, `fetch` (url: GET a URL with the browser's "
        "cookies and return the raw body, a signed-in request without driving the page; method/body "
        "for POST), `profile` (profile: mobile|desktop, or user_agent/width/height: how the browser "
        "presents itself), `close`. After navigate/click/type/login the tool returns the new page state. "
        "When a website asks to sign in, use `login` (or look up credentials with the `passwords` tool and `type` with "
        "`{{vault:LOGIN_<ID>}}`, and `passwords(action='totp')` for 2FA codes). "
        "When no saved login exists or a page needs a CAPTCHA, SMS code or payment confirmation only the user can give, "
        'call `hand_over` with a short `reason` ("sign in to site", "enter SMS code"): the user does it in the browser view, '
        "presses Done, and the tool returns the page as they left it. Leave the browser open "
        "when you finish (no `close` unless asked) so the user can look at or take over the page."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "navigate",
                    "extract",
                    "click",
                    "type",
                    "login",
                    "press",
                    "scroll",
                    "back",
                    "wait",
                    "screenshot",
                    "fetch",
                    "profile",
                    "hand_over",
                    "close",
                ],
            },
            "reason": {
                "type": "string",
                "description": "For hand_over: what the user should do on the page, in their language.",
            },
            "url": {"type": "string"},
            "site": {
                "type": "string",
                "description": "For login: domain, site name, or login id from the password manager (defaults to current page URL).",
            },
            "index": {"type": "integer", "description": "Element number from the last page state."},
            "username_index": {
                "type": "integer",
                "description": "For login: optional element number of the username/email field.",
            },
            "password_index": {
                "type": "integer",
                "description": "For login: optional element number of the password field.",
            },
            "text": {"type": "string"},
            "submit": {"type": "boolean"},
            "key": {"type": "string"},
            "direction": {"type": "string", "enum": ["up", "down"]},
            "seconds": {"type": "number", "description": "For wait: how long, at most 15."},
            "method": {"type": "string", "description": "For fetch: GET (default) or POST."},
            "body": {"type": "string", "description": "For fetch with POST: the request body."},
            "profile": {"type": "string", "enum": ["mobile", "desktop"]},
            "user_agent": {"type": "string"},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
        },
        "required": ["action"],
    }
    risk: RiskLevel = RiskLevel.MODERATE
    egress: bool = True
    accepts_secrets: bool = True

    headless: bool = True
    timeout_ms: int = 30_000
    workspace: Path = Path("./workspace")
    # "auto" | "playwright" | "chrome" | "device"
    backend_mode: str = "auto"
    # the phone link, for the device backend; set by the app
    link: Any = None
    # the Chrome extension bridge, when a Chrome tab is shared; set by the service
    chrome_bridge: Any = None
    # encrypted PasswordStore so `action="login"` can auto-fill saved credentials
    passwords: Any = None
    # the starting profile: "desktop" for Playwright, the phone's own for the device
    default_profile: str = ""
    # receives a BrowserFrame after every action; set by the app
    on_frame: Callable[[BrowserFrame], None] | None = None
    frame_quality: int = 55
    # the holds registry (nanomuse.agent.holds): the tool waits while the user has the page
    holds: Any = None
    hand_over_timeout: float = HAND_OVER_TIMEOUT_S
    # An embedding with a browser of its own (a remote browser service, say) hands the tool a
    # factory: a new session takes the backend it returns instead of Playwright or the phone,
    # and that backend is driven, shown in the app and closed like the two built-in ones. The
    # Sentinel sees every URL either way. A Python integration point, not a setting.
    backend_factory: Callable[[], BrowserBackend] | None = None

    _backend: Any = PrivateAttr(default=None)
    _lock: Any = PrivateAttr(default=None)
    # what the user did since the model last looked at the page
    _user_actions: list[str] = PrivateAttr(default_factory=list)
    # the host of the page last summarised, for the Sentinel's view of the next action
    _last_host: str | None = PrivateAttr(default=None)

    # ------------------------------------------------------------------ lifecycle
    @property
    def profile_dir(self) -> Path:
        """Playwright's persistent profile: cookies and logins, kept under the workspace."""
        return self.workspace / "browser-profile"

    def _pick_backend(self) -> BrowserBackend:
        """The backend for a new session. A running one is kept until it is closed."""
        current = self._backend
        if current is not None and current.open:
            return current
        if self.backend_factory is not None:
            supplied = self.backend_factory()
            if not isinstance(supplied, BrowserBackend):
                raise RuntimeError("the browser's backend_factory did not return a BrowserBackend")
            self._backend = supplied
            return supplied
        mode = self.backend_mode
        chrome_ready = bool(self.chrome_bridge is not None and getattr(self.chrome_bridge, "ready", False))
        device_ready = self.link is not None and self.link.browser_device is not None
        if mode == "chrome" or (mode == "auto" and chrome_ready):
            if not chrome_ready:
                raise RuntimeError(
                    "no Chrome tab is shared with MiniMuse; open the MiniMuse Chrome extension "
                    "and click Share active tab"
                )
            backend: BrowserBackend = ChromeBridgeBackend(
                self.chrome_bridge,
                timeout_s=self.timeout_ms / 1000,
                profile=profile_named(self.default_profile) if self.default_profile else None,
            )
        elif mode == "device" or (mode == "auto" and not playwright_available()):
            if not device_ready:
                raise RuntimeError(
                    "no browser is available: playwright is not installed here and no phone "
                    "with the MiniMuse app is connected"
                    if mode == "auto"
                    else "no phone with the MiniMuse app is connected to lend its browser"
                )
            backend = DeviceBackend(
                self.link,
                timeout_s=self.timeout_ms / 1000,
                profile=profile_named(self.default_profile) if self.default_profile else None,
            )
        else:
            if not playwright_available():
                raise RuntimeError(
                    "playwright is not installed. Run: pip install 'nanomuse[browser]' && playwright install chromium"
                )
            backend = PlaywrightBackend(
                self.profile_dir,
                headless=self.headless,
                timeout_ms=self.timeout_ms,
                profile=profile_named(self.default_profile or "desktop"),
            )
        self._backend = backend
        return backend

    @property
    def backend(self) -> BrowserBackend | None:
        return self._backend

    @property
    def backend_kind(self) -> str:
        """Which backend a session would use now: "playwright", "chrome", "device",
        an embedding's own ("custom" until it is made, then its kind) or ""."""
        if self._backend is not None and self._backend.open:
            return str(self._backend.kind)
        if self.backend_factory is not None:
            return "custom"
        chrome_ready = bool(self.chrome_bridge is not None and getattr(self.chrome_bridge, "ready", False))
        if self.backend_mode == "chrome":
            return "chrome" if chrome_ready else ""
        if self.backend_mode == "device":
            return "device" if self.link is not None and self.link.browser_device else ""
        if self.backend_mode == "auto" and chrome_ready:
            return "chrome"
        if playwright_available():
            return "playwright"
        return "device" if self.link is not None and self.link.browser_device else ""

    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def open(self) -> bool:
        return self._backend is not None and self._backend.open

    @property
    def viewport(self) -> dict[str, int]:
        return self._backend.profile.viewport if self._backend is not None else VIEWPORT

    async def _frame(
        self, backend: BrowserBackend, action: str, by_user: bool = False, thread: str | None = None
    ) -> None:
        if self.on_frame is None:
            return
        try:
            jpeg = await backend.screenshot(self.frame_quality)
            url = await backend.url()
            title = await backend.title()
        except Exception as exc:  # noqa: BLE001
            logger.debug("browser frame: {}", exc)
            return
        if not jpeg:
            return
        try:
            self.on_frame(
                BrowserFrame(
                    url,
                    title,
                    action,
                    jpeg,
                    by_user,
                    thread,
                    backend=backend.kind,
                    width=backend.profile.width,
                    height=backend.profile.height,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("browser frame listener failed: {}", exc)

    async def _label(self, backend: BrowserBackend, index: int) -> str:
        try:
            text = await backend.evaluate(_LABEL_JS, int(index))
        except Exception:  # noqa: BLE001
            return f"[{index}]"
        text = " ".join(str(text or "").split())
        return f"'{text[:40]}'" if text else f"[{index}]"

    async def cleanup(self) -> None:
        backend = self._backend
        self._backend = None
        if backend is not None:
            await backend.close()

    # ------------------------------------------------------------------ sentinel
    def assess(self, args: dict[str, Any]) -> CallAssessment:
        action = args.get("action", "")
        target = (
            host_of(str(args.get("url", "")))
            if action in ("navigate", "fetch")
            else self._current_host()
        )
        detail = (
            args.get("url")
            or args.get("site")
            or args.get("text")
            or args.get("index")
            or args.get("key")
            or args.get("profile")
            or ""
        )
        if action == "hand_over":
            return CallAssessment(
                risk=RiskLevel.SAFE,
                egress=False,
                summary=f"browser.hand_over {str(args.get('reason') or '')[:120]}".strip(),
            )
        return CallAssessment(
            risk=RiskLevel.MODERATE,
            egress=action not in ("extract", "screenshot", "close", "scroll", "profile"),
            egress_target=target,
            summary=f"browser.{action} {str(detail)[:120]}".strip(),
        )

    def _current_host(self) -> str | None:
        return self._last_host

    # ------------------------------------------------------------------ execution
    async def execute(
        self,
        action: str = "",
        url: str | None = None,
        site: str | None = None,
        index: int | None = None,
        username_index: int | None = None,
        password_index: int | None = None,
        text: str | None = None,
        submit: bool | None = None,
        key: str | None = None,
        direction: str = "down",
        seconds: float | None = None,
        method: str | None = None,
        body: str | None = None,
        profile: str | None = None,
        user_agent: str | None = None,
        width: int | None = None,
        height: int | None = None,
        reason: str | None = None,
        **_: Any,
    ) -> ToolResult:
        if action == "close":
            await self.cleanup()
            return ToolResult(output="Browser closed.")
        if action == "hand_over":
            return await self._hand_over(reason)
        # the user has the page: wait for their Done, then look again before acting
        if self.holds is not None and await self.holds.wait(self.holds.thread(), "browser"):
            self._user_actions.append("used the page themselves")
        async with self.lock:
            try:
                backend = self._pick_backend()
            except RuntimeError as exc:
                return ToolResult.fail(str(exc))
            try:
                if action == "fetch":
                    return await self._fetch(backend, url, method, body)
                if action == "profile":
                    return await self._profile(backend, profile, user_agent, width, height)
                if action == "login":
                    return await self._login(
                        backend,
                        site=site or url,
                        username_index=username_index,
                        password_index=password_index,
                        submit=True if submit is None else bool(submit),
                    )
                return await self._act(
                    backend,
                    action,
                    url,
                    index,
                    text,
                    bool(submit),
                    key,
                    direction,
                    seconds,
                )
            except Exception as exc:  # noqa: BLE001 – playwright raises many error types
                return ToolResult.fail(
                    f"browser error: {type(exc).__name__}: {str(exc).splitlines()[0][:300]}"
                )

    async def _act(
        self,
        backend: BrowserBackend,
        action: str,
        url: str | None,
        index: int | None,
        text: str | None,
        submit: bool,
        key: str | None,
        direction: str,
        seconds: float | None = None,
    ) -> ToolResult:
        await backend.ensure()
        await backend.set_url_guard(self._url_allowed)
        if action == "navigate":
            if not url:
                return ToolResult.fail("`url` is required")
            if not url.lower().startswith(("http://", "https://")):
                url = "https://" + url
            if not await self._url_allowed(url):
                return ToolResult.fail(f"refusing to navigate to a private or local address: {url}")
            await backend.goto(url)
            final_url = await backend.url()
            if not await self._url_allowed(final_url):
                return ToolResult.fail(
                    "refusing a navigation whose final URL is a private or local address: "
                    f"{final_url}"
                )
            await self._frame(backend, f"Opened {host_of(url) or url}")
            return await self._state(backend, brief=True)
        if action == "extract":
            await self._frame(backend, "Read the page")
            return await self._state(backend, brief=False)
        if action == "click":
            if index is None:
                return ToolResult.fail("`index` is required")
            label = await self._label(backend, int(index))
            await backend.click_element(int(index))
            await backend.settle(self.timeout_ms)
            await self._frame(backend, f"Clicked {label}")
            return await self._state(backend, brief=True)
        if action == "type":
            if index is None or text is None:
                return ToolResult.fail("`index` and `text` are required")
            label = await self._label(backend, int(index))
            await backend.fill_element(int(index), text)
            if submit:
                await backend.press("Enter")
                await backend.settle(self.timeout_ms)
            await self._frame(backend, f"Typed into {label}" + (" and submitted" if submit else ""))
            return await self._state(backend, brief=True)
        if action == "press":
            await backend.press(key or "Enter")
            await backend.settle(self.timeout_ms)
            await self._frame(backend, f"Pressed {key or 'Enter'}")
            return await self._state(backend, brief=True)
        if action == "scroll":
            step = backend.profile.height * 0.85
            await backend.scroll(-step if direction == "up" else step)
            await self._frame(backend, f"Scrolled {direction}")
            return await self._state(backend, brief=True)
        if action == "back":
            await backend.back()
            await backend.settle(self.timeout_ms)
            await self._frame(backend, "Went back")
            return await self._state(backend, brief=True)
        if action == "wait":
            # A single-page app that draws itself after `load`: slice the sleep so a user
            # taking over interrupts immediately and live viewers see frames as it settles.
            try:
                pause = float(seconds) if seconds is not None else 2.0
            except (TypeError, ValueError):
                pause = 2.0
            remaining = min(max(pause, 0.5), 15.0)
            thread = self.holds.thread() if self.holds is not None else None
            last_frame_at = 0.0
            elapsed = 0.0
            while remaining > 0:
                if thread is not None and self.holds is not None and self.holds.active(thread, "browser"):
                    break
                step = min(0.5, remaining)
                await asyncio.sleep(step)
                remaining -= step
                elapsed += step
                if elapsed - last_frame_at >= 0.75:
                    last_frame_at = elapsed
                    await self._frame(backend, "Waiting for the page…", thread=thread)
            await backend.settle(self.timeout_ms)
            await self._frame(backend, "Waited for the page")
            return await self._state(backend, brief=True)
        if action == "screenshot":
            shots = self.workspace / "screenshots"
            shots.mkdir(parents=True, exist_ok=True)
            data = await backend.screenshot(85)
            # two screenshots inside one second (click, then look again) are two pictures:
            # the second takes the next free name, not the first one's file
            stem = time.strftime("%Y%m%d-%H%M%S")
            path = shots / f"{stem}.jpg"
            n = 2
            while path.exists():
                path = shots / f"{stem}-{n}.jpg"
                n += 1
            path.write_bytes(data)
            await self._frame(backend, "Took a screenshot")
            return ToolResult(output=f"Screenshot saved to {path}", system=str(path))
        return ToolResult.fail(f"unknown action '{action}'")

    async def _login(
        self,
        backend: BrowserBackend,
        site: str | None = None,
        username_index: int | None = None,
        password_index: int | None = None,
        submit: bool = True,
    ) -> ToolResult:
        """Auto-fill username & password from PasswordStore into the current login page."""
        await backend.ensure()
        await backend.set_url_guard(self._url_allowed)
        current_url = await backend.url()
        target = (site or "").strip() or host_of(current_url) or current_url
        if self.passwords is None:
            return ToolResult.fail("Password manager store is not attached to the browser tool.")
        cred = self.passwords.find_best(target)
        if not cred:
            return ToolResult.fail(
                f"No saved login found in the password manager matching '{target}'. "
                "Use `passwords(action='list')` to inspect saved logins, or `hand_over` to let the user sign in."
            )
        username = str(cred.get("username") or "")
        password = str(cred.get("password") or "")
        elements: list[dict[str, Any]] = await backend.evaluate(_ANNOTATE_JS, 80) or []

        def _detect_fields(els: list[dict[str, Any]]) -> tuple[int | None, int | None]:
            u_idx: int | None = None
            p_idx: int | None = None
            for el in els:
                tag = str(el.get("tag") or "").lower()
                etype = str(el.get("type") or "").lower()
                label = str(el.get("text") or "").lower()
                idx = int(el.get("i", -1))
                if tag not in ("input", "textarea"):
                    continue
                if etype in ("hidden", "submit", "button", "checkbox", "radio", "file"):
                    continue
                if p_idx is None and (etype == "password" or "pass" in label or "pwd" in label):
                    p_idx = idx
                    continue
                if u_idx is None and (
                    etype in ("email", "text", "tel", "")
                    and (
                        any(k in label for k in ("user", "email", "login", "account", "phone", "id"))
                        or etype == "email"
                    )
                ):
                    u_idx = idx
            if u_idx is None:
                for el in els:
                    if str(el.get("tag") or "").lower() == "input" and str(
                        el.get("type") or ""
                    ).lower() in ("text", "email", ""):
                        idx = int(el.get("i", -1))
                        if idx != p_idx:
                            u_idx = idx
                            break
            return u_idx, p_idx

        det_u, det_p = _detect_fields(elements)
        u_idx = username_index if username_index is not None else det_u
        p_idx = password_index if password_index is not None else det_p

        filled: list[str] = []
        if u_idx is not None and username:
            await backend.fill_element(int(u_idx), username)
            filled.append(f"username into [{u_idx}]")
            if p_idx is None and submit:
                # Multi-step login (e.g. Google/Microsoft where password appears on step 2)
                await backend.press("Enter")
                await backend.settle(self.timeout_ms)
                await asyncio.sleep(1.0)
                elements2: list[dict[str, Any]] = await backend.evaluate(_ANNOTATE_JS, 80) or []
                _, det_p2 = _detect_fields(elements2)
                if det_p2 is not None:
                    p_idx = det_p2

        if p_idx is not None and password:
            await backend.fill_element(int(p_idx), password)
            filled.append(f"password into [{p_idx}]")
            if submit:
                await backend.press("Enter")
                await backend.settle(self.timeout_ms)

        if not filled:
            return ToolResult.fail(
                "Could not automatically locate username or password input fields on this page. "
                "Call `browser(action='extract')` to see element numbers and pass `username_index` / `password_index`."
            )
        await self._frame(backend, f"Signed in as {username or cred.get('title')}")
        state = await self._state(backend, brief=True)
        prefix = f"Filled {', '.join(filled)} for '{cred.get('title')}' ({username}).\n\n"
        return ToolResult(output=prefix + state.output)

    async def _url_allowed(self, url: str) -> bool:
        """Whether a browser network request stays on globally routable HTTP(S)."""
        try:
            scheme = urlparse(url).scheme.lower()
        except ValueError:
            return False
        if scheme not in ("http", "https", "ws", "wss"):
            return True
        host = host_of(url)
        if not host:
            return False
        return not await asyncio.to_thread(_is_private_host, host)

    async def _fetch(
        self, backend: BrowserBackend, url: str | None, method: str | None, body: str | None
    ) -> ToolResult:
        if not url:
            return ToolResult.fail("`url` is required")
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        # the same guard as web_fetch: the browser's fetch must not reach the machine's own
        # network (loopback, LAN, cloud metadata) on the model's say-so
        host = host_of(url)
        if host is None or await asyncio.to_thread(_is_private_host, host):
            return ToolResult.fail(f"refusing to fetch a private or local address: {url}")
        result = await backend.fetch(url, method=(method or "GET").upper(), body=body)
        # a public page may answer with a redirect into the LAN; each hop gets the same look
        for _ in range(FETCH_MAX_HOPS):
            location = _redirect_target(result, url)
            if location is None:
                break
            next_host = host_of(location)
            if next_host is None or await asyncio.to_thread(_is_private_host, next_host):
                return ToolResult.fail(
                    f"refusing to follow a redirect to a private or local address: {location}"
                )
            url = location
            result = await backend.fetch(url, method="GET")
        text = str(result.get("body") or "")
        ctype = str((result.get("headers") or {}).get("content-type", ""))
        note = ""
        if len(text) > FETCH_MAX_CHARS:
            note = f"\n... [truncated, {len(text)} chars]"
            text = text[:FETCH_MAX_CHARS]
        head = f"HTTP {result.get('status')} {result.get('url') or url}"
        if ctype:
            head += f" ({ctype.split(';')[0].strip()})"
        return ToolResult(output=f"{head}\n\n{text}{note}")

    async def _profile(
        self,
        backend: BrowserBackend,
        profile: str | None,
        user_agent: str | None,
        width: int | None,
        height: int | None,
    ) -> ToolResult:
        prof: BrowserProfile = profile_named(
            profile or "", user_agent, width, height, base=backend.profile
        )
        await backend.set_profile(prof)
        ua = prof.user_agent or "the browser's own"
        return ToolResult(
            output=f"Profile: {prof.name}, {prof.width}×{prof.height}, user agent {ua}."
        )

    # ------------------------------------------------------------------ the user takes over
    async def _hand_over(self, reason: str | None) -> ToolResult:
        """The agent gives the page to the user (contract C1): a hold goes on with the reason,
        the user does their part and presses Done, and the page comes back as they left it."""
        reason = " ".join(str(reason or "").split())
        if not reason:
            return ToolResult.fail("`reason` is required: say what the user should do on the page")
        if self.holds is None:
            return ToolResult.fail(
                "hand_over is not available here; ask the user with `ask_user` to do it and tell "
                "you when it is done"
            )
        thread = self.holds.thread()
        try:
            backend = self._pick_backend()
            await backend.ensure()
            await self._frame(backend, f"Your turn: {reason[:60]}", thread=thread)
        except Exception as exc:  # noqa: BLE001 — the page may not be open yet; the hold still works
            logger.debug("hand_over frame: {}", exc)
        finished = await self.holds.hand_over(
            thread, "browser", reason, timeout=self.hand_over_timeout
        )
        note = took_over_note("browser", finished)
        async with self.lock:
            try:
                backend = self._pick_backend()
                await backend.ensure()
                await self._frame(backend, "You handed the page back" if finished else "Live view")
                state = await self._state(backend, brief=True)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(output=note) if finished else ToolResult.fail(f"{note} ({exc})")
        return ToolResult(output=f"{note}\n\n{state.output}", error=state.error)

    async def user_action(
        self,
        action: str,
        thread: str | None = None,
        *,
        x: float | None = None,
        y: float | None = None,
        text: str | None = None,
        key: str | None = None,
        dy: float | None = None,
        url: str | None = None,
    ) -> dict[str, Any]:
        """An action by the user from the app. ``x``/``y`` are fractions of the frame.

        Not reviewed by the Sentinel: it is the user acting, on their own browser, with
        their own hands. What they did is reported to the model the next time it looks.
        """
        # Do not block long on an in-flight navigation/action when user clicks "Take over".
        if action == "take_over" and self.lock.locked():
            try:
                await asyncio.wait_for(self.lock.acquire(), timeout=0.35)
            except TimeoutError:
                cur_url = ""
                cur_title = "Taking over…"
                if self._backend is not None and self._backend.open:
                    try:
                        cur_url = await self._backend.url()
                    except Exception:  # noqa: BLE001
                        pass
                return {"url": cur_url, "title": cur_title, "pending": True}
            try:
                backend = self._pick_backend()
                await backend.ensure()
                await self._frame(backend, "You have the page", by_user=True, thread=thread)
                return {"url": await backend.url(), "title": await backend.title()}
            finally:
                self.lock.release()
        if action == "look" and self.lock.locked():
            try:
                await asyncio.wait_for(self.lock.acquire(), timeout=0.15)
            except TimeoutError:
                cur_url = ""
                cur_title = ""
                if self._backend is not None and self._backend.open:
                    try:
                        cur_url = await self._backend.url()
                        cur_title = await self._backend.title()
                    except Exception:  # noqa: BLE001
                        pass
                return {"url": cur_url, "title": cur_title, "busy": True}
            try:
                backend = self._pick_backend()
                await backend.ensure()
                await self._frame(backend, "Live view", by_user=True, thread=thread)
                return {"url": await backend.url(), "title": await backend.title()}
            finally:
                self.lock.release()

        async with self.lock:
            backend = self._pick_backend()
            await backend.ensure()
            vp = backend.profile.viewport
            if action == "click":
                if x is None or y is None:
                    raise ValueError("click needs x and y")
                px = min(max(float(x), 0.0), 1.0) * vp["width"]
                py = min(max(float(y), 0.0), 1.0) * vp["height"]
                await backend.click_at(px, py)
                await backend.settle(self.timeout_ms)
                caption, note = "You tapped the page", f"clicked at ({int(px)}, {int(py)})"
            elif action == "type":
                if text is None:
                    raise ValueError("type needs text")
                await backend.type_text(text)
                caption, note = "You typed", f"typed {len(text)} characters"
            elif action == "key":
                await backend.press(key or "Enter")
                await backend.settle(self.timeout_ms)
                caption, note = f"You pressed {key or 'Enter'}", f"pressed {key or 'Enter'}"
            elif action == "scroll":
                await backend.scroll(float(dy if dy is not None else 600))
                caption, note = "You scrolled", "scrolled"
            elif action == "back":
                await backend.back()
                await backend.settle(self.timeout_ms)
                caption, note = "You went back", "went back a page"
            elif action == "reload":
                await backend.evaluate("() => location.reload()")
                await backend.settle(self.timeout_ms)
                caption, note = "You reloaded the page", "reloaded the page"
            elif action == "navigate":
                if not url:
                    raise ValueError("navigate needs a url")
                if not url.lower().startswith(("http://", "https://")):
                    url = "https://" + url
                await backend.goto(url)
                caption, note = f"You opened {host_of(url) or url}", f"opened {url}"
            elif action == "look":
                caption, note = "Live view", ""
            elif action == "take_over":
                # the user has the page from here on (a hold goes on in the service)
                caption, note = "You have the page", ""
            elif action == "handed_back":
                # the take-over sheet closed: the user drove the page themselves
                caption, note = "You handed the page back", "used the page in the app"
            else:
                raise ValueError(f"unknown action '{action}'")
            if note:
                self._user_actions.append(note)
                logger.info("browser: user {}", note)
            await self._frame(backend, caption, by_user=True, thread=thread)
            return {"url": await backend.url(), "title": await backend.title()}

    async def _state(self, backend: BrowserBackend, brief: bool) -> ToolResult:
        url = await backend.url()
        if not await self._url_allowed(url):
            return ToolResult.fail(
                f"refusing a navigation whose final URL is a private or local address: {url}"
            )
        max_text = 3000 if brief else 9000
        max_elements = 60 if brief else 150
        elements = await backend.evaluate(_ANNOTATE_JS, max_elements) or []
        text = await backend.evaluate(_TEXT_JS)
        text = " ".join(str(text).split()) if text else ""
        if len(text) > max_text:
            text = text[:max_text] + f" ... [truncated, {len(text)} chars]"
        self._last_host = host_of(url) if url else None
        lines = []
        if self._user_actions:
            done = "; ".join(self._user_actions[-8:])
            self._user_actions.clear()
            lines += [
                f"Note: the user took over the browser in the app and {done}. "
                "Continue from the page as it is now; do not redo what they did.",
                "",
            ]
        lines += [
            f"URL: {url}",
            f"Title: {await backend.title()}",
            "",
            "## Page text",
            text,
            "",
            "## Interactive elements",
        ]
        for e in elements:
            desc = (
                f"[{e['i']}] <{e['tag']}{(' type=' + e['type']) if e['type'] else ''}> {e['text']}"
            )
            if e.get("href"):
                desc += f" → {e['href']}"
            lines.append(desc)
        if not elements:
            lines.append("(none)")
        return ToolResult(output="\n".join(lines))


__all__ = ["VIEWPORT", "Browser", "BrowserFrame", "playwright_available"]
