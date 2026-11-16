"""Password manager tool for searching saved logins, 2FA TOTP codes, and vault placeholders."""

from __future__ import annotations

from typing import Any

from nanomuse.passwords import PasswordStore
from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool


class PasswordsTool(BaseTool):
    name: str = "passwords"
    description: str = (
        "Access the encrypted password manager to look up saved website logins, usernames, "
        "vault password placeholders ({{vault:LOGIN_<ID>}}), and live 6-digit 2FA/TOTP codes, "
        "or save a new login. Use this before logging into websites with `browser(action='login')` "
        "or when the user asks about their saved accounts."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "list", "totp", "add", "delete"],
                "description": (
                    "'search': find saved logins matching a domain/site/username. "
                    "'list': list all saved logins (usernames & domains, passwords redacted). "
                    "'totp': generate the current 6-digit 2FA/TOTP code for a login. "
                    "'add': save or update a login in the password manager. "
                    "'delete': delete a saved login by id."
                ),
            },
            "query": {
                "type": "string",
                "description": "Domain, website name, URL, or username to search for.",
            },
            "id": {
                "type": "string",
                "description": "Login ID (for 'totp' or 'delete').",
            },
            "title": {
                "type": "string",
                "description": "Site or service title when adding a login.",
            },
            "url": {
                "type": "string",
                "description": "Login URL or domain when adding a login.",
            },
            "username": {
                "type": "string",
                "description": "Username or email when adding or filtering a login.",
            },
            "password": {
                "type": "string",
                "description": "Password when adding a login.",
            },
            "totp": {
                "type": "string",
                "description": "Optional base32 TOTP secret or otpauth:// URI when adding a login.",
            },
            "notes": {
                "type": "string",
                "description": "Optional notes when adding a login.",
            },
        },
        "required": ["action"],
    }
    risk: RiskLevel = RiskLevel.SAFE
    reads_private_data: bool = True
    store: PasswordStore

    async def execute(self, **kwargs: Any) -> ToolResult:
        action = str(kwargs.get("action") or "search").strip().lower()
        if action in ("list", "search"):
            q = str(kwargs.get("query") or kwargs.get("url") or kwargs.get("id") or "").strip()
            items = self.store.list_entries(q)
            if not items and q:
                best = self.store.find_best(q, str(kwargs.get("username") or ""))
                if best:
                    items = [self.store._sanitize(best)]
            if not items:
                if q:
                    return ToolResult(output=f"No saved logins found matching '{q}'.")
                return ToolResult(output="No saved logins in the password manager yet.")
            lines = [f"Found {len(items)} saved login(s):"]
            for it in items[:25]:
                vref = f"{{{{vault:{it['vault_key']}}}}}"
                totp_badge = " [2FA/TOTP available]" if it.get("has_totp") else ""
                lines.append(
                    f"- id={it['id']} | {it['title']} ({it['domain'] or it['url']}) | "
                    f"username: {it['username']} | password_ref: {vref}{totp_badge}"
                )
            lines.append(
                "\nTip: To sign in automatically on the current browser page, call "
                "`browser(action='login', site='<domain_or_id>')`, or fill the password field "
                "using the `password_ref` placeholder above."
            )
            return ToolResult(output="\n".join(lines))

        if action == "totp":
            target = str(kwargs.get("id") or kwargs.get("query") or "").strip()
            if not target:
                return ToolResult.fail("Provide 'id' or 'query' (domain/site) for 'totp'.")
            try:
                code, rem = self.store.totp_code(target)
                return ToolResult(output=f"TOTP 2FA code for '{target}': {code} (valid for {rem}s)")
            except Exception as exc:
                return ToolResult.fail(f"Error generating TOTP code: {exc}")

        if action == "add":
            title = str(kwargs.get("title") or "").strip()
            url = str(kwargs.get("url") or "").strip()
            username = str(kwargs.get("username") or "").strip()
            password = str(kwargs.get("password") or "")
            totp = str(kwargs.get("totp") or "").strip()
            notes = str(kwargs.get("notes") or "").strip()
            if not (title or url) or not (username or password):
                return ToolResult.fail(
                    "'title' (or 'url') and 'username' (or 'password') are required."
                )
            entry, status = self.store.add_or_update(
                title=title,
                url=url,
                username=username,
                password=password,
                totp=totp,
                notes=notes,
                dedupe=True,
            )
            return ToolResult(
                output=(
                    f"Login {status}: id={entry['id']} ({entry['title']}) for user "
                    f"'{entry['username']}'. Password stored encrypted as "
                    f"{{{{vault:{entry['vault_key']}}}}}."
                )
            )

        if action == "delete":
            target = str(kwargs.get("id") or "").strip()
            if not target:
                return ToolResult.fail("'id' is required for 'delete'.")
            ok = self.store.delete(target)
            return (
                ToolResult(output=f"Deleted login '{target}'.")
                if ok
                else ToolResult.fail(f"Login '{target}' not found.")
            )

        return ToolResult.fail(
            f"Unknown action '{action}'. Use 'search', 'list', 'totp', 'add', or 'delete'."
        )
