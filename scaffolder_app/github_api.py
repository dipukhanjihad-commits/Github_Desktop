"""GitHub REST client.

Uses ``urllib`` only, so no extra dependency is needed. Every non-2xx response
becomes a structured :class:`GitHubError` (or :class:`TokenExpiredError`).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .errors import (
    GitHubError, NetworkError, RemoteUnsupportedError, ScaffolderError,
    TokenExpiredError,
)

API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "project-scaffolder"
MAX_PAGES = 5
PER_PAGE = 100


class RemoteInfo:
    """A parsed git remote."""

    __slots__ = ("host", "owner", "repo", "url", "kind")

    def __init__(self, host: str, owner: str, repo: str, url: str,
                 kind: str) -> None:
        self.host = host
        self.owner = owner
        self.repo = repo
        self.url = url
        self.kind = kind

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def is_github(self) -> bool:
        return self.kind == "github"

    def __repr__(self) -> str:
        return f"RemoteInfo({self.full_name!r}, kind={self.kind!r})"


def parse_remote(url: str) -> RemoteInfo:
    """Parse HTTPS, SSH (``git@host:owner/repo``) and ``ssh://`` remotes."""
    raw = (url or "").strip()
    if not raw:
        raise RemoteUnsupportedError(url)
    if raw.startswith("git@") and ":" in raw:
        host, path = raw[4:].split(":", 1)
        scheme = "ssh"
    elif raw.startswith("ssh://"):
        parsed = urllib.parse.urlsplit(raw)
        host = parsed.hostname or ""
        path = parsed.path.lstrip("/")
        scheme = "ssh"
    elif "://" in raw:
        parsed = urllib.parse.urlsplit(raw)
        host = (parsed.hostname or "").lower()
        path = parsed.path.lstrip("/")
        scheme = "https"
    elif "/" in raw and " " not in raw:
        # Bare "owner/repo" shorthand.
        host, path, scheme = "github.com", raw, "https"
    else:
        raise RemoteUnsupportedError(url)

    path = path.split("?", 1)[0].split("#", 1)[0]
    path = path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        raise RemoteUnsupportedError(url)
    owner, repo = parts[0], "/".join(parts[1:])
    host = host.lower()
    kind = "github" if host in ("github.com", "www.github.com") else "other"
    https_url = (f"https://{host}/{owner}/{repo}" if scheme in ("https", "ssh")
                 else raw)
    return RemoteInfo(host, owner, repo, https_url, kind)


def normalize_clone_url(raw: str) -> str:
    """Turn any accepted user input into an HTTPS clone URL."""
    text = (raw or "").strip()
    if not text:
        raise RemoteUnsupportedError(raw)
    try:
        info = parse_remote(text)
    except RemoteUnsupportedError:
        raise
    if info.kind != "github":
        raise RemoteUnsupportedError(raw)
    return info.url + ".git"


class GitHubClient:
    def __init__(self, token_provider) -> None:
        """``token_provider(account) -> str | None``."""
        self._token_provider = token_provider

    # ----------------------------------------------------------- transport

    def _request(self, method: str, path: str, token: str,
                 body: dict | None = None, timeout: int = 30) -> Any:
        url = path if path.startswith("http") else f"{API_ROOT}{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method,
                                         headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                payload = resp.read()
                if resp.status == 204 or not payload:
                    return {}
                return json.loads(payload.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(detail).get("message", detail)
            except (json.JSONDecodeError, AttributeError):
                pass
            if exc.code == 401:
                raise TokenExpiredError("github") from None
            if exc.code == 403 and "rate limit" in detail.lower():
                raise GitHubError(403, "API rate limit exceeded. Try again later.") from None
            raise GitHubError(exc.code, detail) from None
        except urllib.error.URLError as exc:
            raise NetworkError(f"Network error: {exc.reason}") from None
        except TimeoutError:
            raise NetworkError("GitHub request timed out.") from None

    def _token(self, account: str) -> str:
        token = self._token_provider(account)
        if not token:
            raise GitHubError(401, f"No token stored for '{account}'.")
        return token

    # --------------------------------------------------------------- endpoints

    def verify_token(self, token: str) -> dict:
        user = self._request("GET", "/user", token)
        try:
            orgs = self._request("GET", "/user/orgs?per_page=100", token)
        except ScaffolderError:
            orgs = []
        return {
            "login": user.get("login", ""),
            "id": user.get("id", 0),
            "name": user.get("name") or "",
            "avatar_url": user.get("avatar_url", ""),
            "orgs": [o.get("login") for o in orgs if o.get("login")],
        }

    def current_user(self, account: str) -> dict:
        return self._request("GET", "/user", self._token(account))

    def list_repositories(self, account: str, max_pages: int = MAX_PAGES,
                          include_forks: bool = False) -> list[dict]:
        token = self._token(account)
        repos: list[dict] = []
        for page in range(1, max_pages + 1):
            batch = self._request(
                "GET",
                "/user/repos?per_page=%d&sort=updated"
                "&affiliation=owner,collaborator,organization_member&page=%d"
                % (PER_PAGE, page),
                token,
            )
            if not batch:
                break
            repos.extend(batch)
            if len(batch) < PER_PAGE:
                break
        result = []
        for repo in repos:
            if repo.get("fork") and not include_forks:
                continue
            result.append({
                "name": repo["name"],
                "full_name": repo["full_name"],
                "owner": {"login": repo["owner"]["login"]},
                "private": bool(repo["private"]),
                "fork": bool(repo.get("fork")),
                "archived": bool(repo.get("archived")),
                "description": repo.get("description") or "",
                "clone_url": repo["clone_url"],
                "ssh_url": repo.get("ssh_url", ""),
                "html_url": repo["html_url"],
                "updated_at": repo.get("updated_at", ""),
                "default_branch": repo.get("default_branch", "main"),
            })
        return result

    def repo_exists(self, account: str, owner: str, name: str) -> bool:
        try:
            self._request("GET", f"/repos/{owner}/{name}", self._token(account))
            return True
        except GitHubError as exc:
            if exc.status == 404:
                return False
            raise

    def create_repo(self, account: str, owner: str | None, name: str,
                    private: bool, description: str = "") -> dict:
        token = self._token(account)
        me = self._request("GET", "/user", token).get("login", "")
        is_org = bool(owner) and owner != me
        target_owner = owner or me
        if self.repo_exists(account, target_owner, name):
            raise ScaffolderError(
                f"Repository '{target_owner}/{name}' already exists on GitHub.")
        path = f"/orgs/{owner}/repos" if is_org else "/user/repos"
        body = {"name": name, "private": private, "auto_init": False}
        if description:
            body["description"] = description
        return self._request("POST", path, token, body)

    def rename_repo(self, account: str, owner: str, repo: str, new_name: str) -> dict:
        return self._request("PATCH", f"/repos/{owner}/{repo}",
                             self._token(account), {"name": new_name})

    def set_visibility(self, account: str, owner: str, repo: str,
                       private: bool) -> dict:
        return self._request("PATCH", f"/repos/{owner}/{repo}",
                             self._token(account), {"private": private})

    def delete_repo(self, account: str, owner: str, repo: str) -> None:
        self._request("DELETE", f"/repos/{owner}/{repo}", self._token(account))

    def create_pull_request(self, account: str, owner: str, repo: str, head: str,
                            base: str, title: str, body: str = "") -> dict:
        return self._request(
            "POST", f"/repos/{owner}/{repo}/pulls", self._token(account),
            {"title": title, "body": body, "head": head, "base": base})

    def list_pull_requests(self, account: str, owner: str, repo: str,
                           state: str = "open") -> list[dict]:
        return self._request(
            "GET", f"/repos/{owner}/{repo}/pulls?state={state}&per_page=100",
            self._token(account))

    def list_issues(self, account: str, owner: str, repo: str,
                    state: str = "open") -> list[dict]:
        items = self._request(
            "GET", f"/repos/{owner}/{repo}/issues?state={state}&per_page=50",
            self._token(account))
        return [i for i in items if "pull_request" not in i]

    def create_release(self, account: str, owner: str, repo: str, tag: str,
                       name: str = "", body: str = "",
                       draft: bool = False) -> dict:
        payload = {"tag_name": tag, "name": name or tag, "body": body,
                   "draft": draft}
        return self._request("POST", f"/repos/{owner}/{repo}/releases",
                             self._token(account), payload)

    def repo_status(self, account: str, owner: str, repo: str) -> dict:
        """Lightweight project health: open issues and PRs."""
        result = {"open_issues": 0, "open_prs": 0, "error": ""}
        try:
            result["open_prs"] = len(self.list_pull_requests(account, owner, repo))
            result["open_issues"] = len(self.list_issues(account, owner, repo))
        except ScaffolderError as exc:
            result["error"] = str(exc)
        return result


# ---------------------------------------------------------------------------
# OAuth device flow (S9) — avoids asking users to hand-craft PAT scopes.
# ---------------------------------------------------------------------------

DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"


class DeviceFlow:
    """Minimal GitHub OAuth device-flow helper.

    ``client_id`` must be supplied by the user's OAuth app. The scope list is
    requested up front; ``repo`` and ``read:org`` match what the app needs.
    """

    def __init__(self, client_id: str,
                 scopes: str = "repo read:user read:org") -> None:
        self.client_id = client_id
        self.scopes = scopes

    def _post(self, url: str, payload: dict) -> dict:
        data = urllib.parse.urlencode(payload).encode()
        request = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Accept": "application/json", "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise GitHubError(exc.code, "Device flow request rejected.") from None
        except urllib.error.URLError as exc:
            raise NetworkError(f"Network error: {exc.reason}") from None

    def start(self) -> dict:
        payload = self._post(DEVICE_CODE_URL,
                             {"client_id": self.client_id, "scope": self.scopes})
        if "device_code" not in payload:
            raise GitHubError(400, payload.get("error_description", "device flow failed"))
        return {
            "device_code": payload["device_code"],
            "user_code": payload.get("user_code", ""),
            "verification_uri": payload.get("verification_uri",
                                            "https://github.com/login/device"),
            "interval": int(payload.get("interval", 5)),
            "expires_in": int(payload.get("expires_in", 900)),
        }

    def poll_once(self, device_code: str) -> dict | None:
        payload = self._post(TOKEN_URL, {
            "client_id": self.client_id,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })
        if payload.get("access_token"):
            return {"token": payload["access_token"],
                    "scope": payload.get("scope", "")}
        error = payload.get("error", "")
        if error == "authorization_pending":
            return None
        if error == "slow_down":
            return {"slow_down": True}
        if error == "expired_token":
            raise GitHubError(400, "The device code expired. Start again.")
        raise GitHubError(400, payload.get("error_description", error or "unknown"))
