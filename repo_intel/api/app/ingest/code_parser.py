import ast
import hashlib
import os
import re

from .deps import DEP_UPSERT, parse_manifest
from .git_log import language_of, should_index
from .limits import CODE_LANGS, MAX_FILE_BYTES, MAX_FILES, MAX_UNITS, SKIP_DIRS


def split_identifier(s: str) -> str:
    """validateToken / validate_token / auth/session.py -> words, so FULLTEXT can match them."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    return re.sub(r"[_\-./]+", " ", s).lower()


def read_text(full: str):
    try:
        with open(full, "rb") as f:
            raw = f.read(MAX_FILE_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_FILE_BYTES or b"\0" in raw[:4096]:       # too large or binary
        return None
    return raw.decode("utf-8", errors="replace")


def python_units(text: str):
    lines = text.splitlines()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sig = f"def {node.name}({ast.unparse(node.args)})"
            if node.returns:
                sig += f" -> {ast.unparse(node.returns)}"
            yield {"name": node.name, "kind": "function", "signature": sig,
                   "start": node.lineno, "end": node.end_lineno,
                   "doc": ast.get_docstring(node) or "",
                   "snippet": "\n".join(lines[node.lineno - 1: node.lineno + 39])}


def window_units(rel: str, text: str, size: int = 40, step: int = 30, cap: int = 60):
    """Fallback for non-Python files: overlapping line windows."""
    lines, base = text.splitlines(), rel.rsplit("/", 1)[-1]
    for n, s in enumerate(range(0, len(lines), step)):
        if n >= cap:
            break
        chunk = lines[s:s + size]
        if "".join(chunk).strip():
            yield {"name": f"{base}:{s + 1}-{s + len(chunk)}", "kind": "window", "signature": "",
                   "start": s + 1, "end": s + len(chunk), "doc": "", "snippet": "\n".join(chunk)}


def extract_units(rel: str, text: str, lang: str) -> list:
    if lang == "Python":
        try:
            return list(python_units(text))
        except (SyntaxError, ValueError, RecursionError):
            pass                                                # Python 2, broken file: use windows
    return list(window_units(rel, text))


def walk_indexable(repo_dir: str):
    for root, dirs, names in os.walk(repo_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for n in sorted(names):                                 # sorted: deterministic when capped
            full = os.path.join(root, n)
            rel = os.path.relpath(full, repo_dir).replace(os.sep, "/")
            if not os.path.islink(full) and should_index(rel):
                yield rel, full


def ingest_files(cur, repo_id: int, repo_dir: str):
    """One transaction: either the whole file/function/dependency state updates, or none of it."""
    conn = cur.connection
    conn.begin()
    try:
        warning = _ingest_files(cur, repo_id, repo_dir)
        conn.commit()
        return warning
    except Exception:
        conn.rollback()
        raise


def _ingest_files(cur, repo_id: int, repo_dir: str):
    cur.execute("UPDATE files SET exists_at_head=0 WHERE repo_id=%s", (repo_id,))
    warning, units_total, seen_deps = None, 0, set()

    for i, (rel, full) in enumerate(walk_indexable(repo_dir)):
        if i >= MAX_FILES:
            warning = f"Stopped at {MAX_FILES} files; the rest of the repo was not analyzed"
            break
        text = read_text(full)
        if text is None:
            continue

        for eco, name, spec, is_dev in parse_manifest(rel, text):
            cur.execute(DEP_UPSERT, (repo_id, eco, name, spec, is_dev, rel))
            seen_deps.add((eco, name, rel))

        lang = language_of(rel)
        digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        cur.execute("SELECT file_id, content_hash FROM files WHERE repo_id=%s AND file_path=%s",
                    (repo_id, rel))
        row = cur.fetchone()
        if row and row["content_hash"] == digest:               # unchanged: keep functions + vectors
            cur.execute("UPDATE files SET exists_at_head=1 WHERE file_id=%s", (row["file_id"],))
            continue

        is_code = lang in CODE_LANGS
        if is_code and units_total >= MAX_UNITS:
            warning = warning or f"Function limit ({MAX_UNITS}) reached; remaining files were not analyzed"
            continue

        cur.execute(
            """INSERT INTO files (repo_id, file_path, language, size_bytes, line_count, exists_at_head)
               VALUES (%s,%s,%s,%s,%s,1)
               ON DUPLICATE KEY UPDATE file_id = LAST_INSERT_ID(file_id), language = VALUES(language),
                 size_bytes = VALUES(size_bytes), line_count = VALUES(line_count), exists_at_head = 1""",
            (repo_id, rel, lang, os.path.getsize(full), text.count("\n") + 1))
        fid = cur.lastrowid                                     # LAST_INSERT_ID trick: no extra SELECT
        cur.execute("DELETE FROM functions WHERE file_id=%s", (fid,))     # cascades to embeddings

        if is_code:
            units = extract_units(rel, text, lang)[: MAX_UNITS - units_total]
            units_total += len(units)
            path_words = split_identifier(rel)
            cur.executemany(
                """INSERT INTO functions (file_id, name, name_tokens, kind, signature,
                                          start_line, end_line, docstring, snippet)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                [(fid, u["name"][:255], (split_identifier(u["name"]) + " " + path_words)[:500],
                  u["kind"], u["signature"][:1000], u["start"], u["end"],
                  u["doc"], u["snippet"][:4000]) for u in units])

        cur.execute("UPDATE files SET content_hash=%s WHERE file_id=%s", (digest, fid))  # set LAST

    if warning is None:                                         # prune only after a complete walk
        cur.execute("SELECT dep_id, ecosystem, name, manifest_path FROM dependencies WHERE repo_id=%s",
                    (repo_id,))
        stale = [r["dep_id"] for r in cur.fetchall()
                 if (r["ecosystem"], r["name"], r["manifest_path"]) not in seen_deps]
        if stale:
            cur.execute(f"DELETE FROM dependencies WHERE dep_id IN ({','.join(['%s'] * len(stale))})", stale)
        cur.execute("""DELETE fn FROM functions fn JOIN files f ON f.file_id = fn.file_id
                       WHERE f.repo_id=%s AND f.exists_at_head=0""", (repo_id,))
        cur.execute("UPDATE files SET content_hash=NULL WHERE repo_id=%s AND exists_at_head=0",
                    (repo_id,))                                 # so a restored file is re-indexed
    return warning