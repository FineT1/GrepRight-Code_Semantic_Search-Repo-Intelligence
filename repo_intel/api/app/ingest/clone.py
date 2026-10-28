import os
import re
import subprocess
from urllib.parse import urlparse

from .limits import ALLOWED_HOSTS, CLONE_TIMEOUT, MAX_COMMITS, MAX_REPO_BYTES

_PATH_RE = re.compile(r"/([\w.\-]+)/([\w.\-]+?)(\.git)?/?")


def validate_url(url: str) -> str:
    """Accept only public https URLs like https://github.com/owner/repo."""
    url = url.strip()
    try:
        u = urlparse(url)
        port = u.port
    except ValueError:
        raise ValueError("Invalid URL")
    m = _PATH_RE.fullmatch(u.path or "")
    if (u.scheme != "https" or u.hostname not in ALLOWED_HOSTS or port is not None
            or u.username or u.password or u.query or u.fragment or not m
            or set(m.group(1)) <= {"."} or set(m.group(2)) <= {"."}):
        raise ValueError("Only public https URLs like https://github.com/owner/repo are supported")
    return url


def repo_name(url: str) -> str:
    return _PATH_RE.fullmatch(urlparse(url).path).group(2)


def git(repo_dir: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", "-C", repo_dir, *args],
        capture_output=True, encoding="utf-8", errors="replace",
        timeout=CLONE_TIMEOUT, check=True).stdout


def dir_size(path: str) -> int:
    total = 0
    for root, _, names in os.walk(path):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(root, n))
            except OSError:
                pass
    return total


def clone(url: str, dest: str) -> None:
    try:
        subprocess.run(
            ["git", "-c", "credential.helper=", "clone", "--single-branch", "--no-tags",
             "--no-recurse-submodules", "--depth", str(MAX_COMMITS), url, dest],
            check=True, timeout=CLONE_TIMEOUT, capture_output=True,
            encoding="utf-8", errors="replace",
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})   # never prompt for credentials
    except subprocess.TimeoutExpired:
        raise RuntimeError("git clone timed out (repository too large)")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"git clone failed: {(e.stderr or '').strip()[-300:]}")
    if dir_size(dest) > MAX_REPO_BYTES:
        raise RuntimeError("Repository is larger than the supported size")