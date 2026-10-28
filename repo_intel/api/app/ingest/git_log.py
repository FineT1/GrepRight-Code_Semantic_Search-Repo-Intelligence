import json
from datetime import timezone, datetime

from .clone import git
from .limits import LANG, MANIFESTS, MAX_COMMITS, MAX_PATH_LEN, SKIP_DIRS, SKIP_SUFFIXES


def language_of(path: str):
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return None
    return LANG.get("." + name.rsplit(".", 1)[-1].lower())


def should_index(rel: str) -> bool:
    if len(rel) > MAX_PATH_LEN or rel.startswith('"'):      # quoted path = odd characters, skip
        return False
    parts = rel.split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]) or rel.endswith(SKIP_SUFFIXES):
        return False
    return language_of(rel) is not None or parts[-1] in MANIFESTS


def read_history(repo_dir: str):
    """Returns (commits, truncated). Uses .mailmap via %aN/%aE; --no-renames keeps paths simple."""
    fmt = "%x1e%H%x1f%aN%x1f%aE%x1f%aI%x1f%s"
    out = git(repo_dir, "log", "--no-renames", "--numstat", f"--pretty=format:{fmt}")
    commits = []
    for rec in out.split("\x1e")[1:]:
        lines = rec.split("\n")
        head = lines[0].split("\x1f", 4)
        if len(head) != 5:
            continue
        h, name, email, ts, subject = head
        try:
            when = datetime.fromisoformat(ts).astimezone(timezone.utc)
        except ValueError:
            continue
        files = []
        for ln in lines[1:]:
            parts = ln.split("\t", 2)
            if len(parts) == 3:                              # "-" marks a binary file: count as 0
                a, d, p = parts
                files.append({"path": p, "add": int(a) if a.isdigit() else 0,
                              "del": int(d) if d.isdigit() else 0})
        commits.append({"hash": h, "author_name": name[:255], "author_email": email.lower()[:255],
                        "message": subject[:500], "time": when.strftime("%Y-%m-%d %H:%M:%S"),
                        "files": files})
    truncated = git(repo_dir, "rev-parse", "--is-shallow-repository").strip() == "true"
    if truncated and commits:
        commits.pop()      # the oldest commit of a shallow clone looks like "added everything"
    return commits, truncated


def upsert_files(cur, repo_id: int, paths) -> dict:
    cur.executemany(
        "INSERT INTO files (repo_id, file_path, language) VALUES (%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE language = VALUES(language)",
        [(repo_id, p, language_of(p)) for p in paths])
    cur.execute("SELECT file_id, file_path FROM files WHERE repo_id=%s", (repo_id,))
    return {r["file_path"]: r["file_id"] for r in cur.fetchall()}


def ingest_history(cur, repo_id: int, repo_dir: str):
    commits, truncated = read_history(repo_dir)
    cur.execute("SELECT commit_hash FROM commits WHERE repo_id=%s", (repo_id,))
    known = {r["commit_hash"] for r in cur.fetchall()}
    new = [c for c in commits if c["hash"] not in known]        # makes re-imports cheap

    paths = {f["path"] for c in new for f in c["files"] if should_index(f["path"])}
    ids = upsert_files(cur, repo_id, paths)

    for i in range(0, len(new), 200):                           # one transaction per 200 commits
        batch = [{
            "hash": c["hash"], "author_name": c["author_name"], "author_email": c["author_email"],
            "message": c["message"], "committed_at": c["time"],
            "additions": sum(f["add"] for f in c["files"]),
            "deletions": sum(f["del"] for f in c["files"]),
            "files": [{"file_id": ids[f["path"]], "additions": f["add"], "deletions": f["del"]}
                      for f in c["files"] if f["path"] in ids]}
            for c in new[i:i + 200]]
        cur.execute("CALL sp_record_commit_batch(%s, CAST(%s AS JSON))", (repo_id, json.dumps(batch)))

    return f"History limited to the latest {MAX_COMMITS} commits" if truncated else None