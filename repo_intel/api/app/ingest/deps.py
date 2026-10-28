import json
import re

import requests

DEP_UPSERT = """
INSERT INTO dependencies (repo_id, ecosystem, name, version_spec, is_dev, manifest_path)
VALUES (%s,%s,%s,%s,%s,%s)
ON DUPLICATE KEY UPDATE
  checked_at   = IF(version_spec = VALUES(version_spec), checked_at, NULL),
  version_spec = VALUES(version_spec),
  is_dev       = VALUES(is_dev)"""        # assignment order matters: checked_at reads the OLD spec


def norm(eco: str, name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower() if eco == "PyPI" else name


def parse_manifest(rel: str, text: str):
    """Returns [(ecosystem, name, version_spec, is_dev)]."""
    base, deps = rel.rsplit("/", 1)[-1], []
    if base == "requirements.txt":
        for line in text.splitlines():
            line = line.split("#")[0].strip()
            if not line or line.startswith(("-", "git+", "http")):
                continue
            m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*(.*)$", line)
            if m:
                spec = m.group(2).split(";")[0].strip().rstrip("\\").strip()
                deps.append(("PyPI", norm("PyPI", m.group(1)), spec[:100], False))
    elif base == "package.json":
        try:
            data = json.loads(text)
        except ValueError:
            return []
        if not isinstance(data, dict):
            return []
        for section, dev in (("dependencies", False), ("devDependencies", True)):
            block = data.get(section)
            if isinstance(block, dict):
                for name, spec in block.items():
                    deps.append(("npm", str(name)[:200], str(spec)[:100], dev))
    return deps


def checkable_version(eco: str, spec: str):
    """Only exact versions. npm ^1.2.3 / ~1.2.3 are approximated by 1.2.3 (say so in the UI)."""
    pattern = r"^==\s*([\w.\-]+)$" if eco == "PyPI" else r"^[\^~]?(\d+\.\d+\.\d+)$"
    m = re.match(pattern, spec.strip())
    return m.group(1) if m else None


def check_osv(http, eco: str, name: str, version: str):
    r = http.post("https://api.osv.dev/v1/query", timeout=(3, 10),
                  json={"package": {"name": name, "ecosystem": eco}, "version": version})
    r.raise_for_status()
    return [(v["id"], (v.get("summary") or "")[:500]) for v in r.json().get("vulns", [])]


def check_dependencies(cur, repo_id: int, cap: int = 200) -> None:
    cur.execute("""SELECT dep_id, ecosystem, name, version_spec FROM dependencies
                   WHERE repo_id=%s AND (checked_at IS NULL OR checked_at < NOW() - INTERVAL 7 DAY)""",
                (repo_id,))
    todo = [(d, v) for d in cur.fetchall()
            if (v := checkable_version(d["ecosystem"], d["version_spec"]))][:cap]
    fails = 0
    with requests.Session() as http:
        for d, version in todo:
            try:
                vulns = check_osv(http, d["ecosystem"], d["name"], version)
            except requests.RequestException:
                fails += 1
                if fails >= 3:
                    break                        # offline or rate-limited: stop waiting on timeouts
                continue
            fails = 0
            cur.execute("DELETE FROM dependency_findings WHERE dep_id=%s", (d["dep_id"],))
            cur.executemany("INSERT IGNORE INTO dependency_findings (dep_id, osv_id, summary) "
                            "VALUES (%s,%s,%s)", [(d["dep_id"], oid, s) for oid, s in vulns])
            cur.execute("UPDATE dependencies SET checked_at=NOW() WHERE dep_id=%s", (d["dep_id"],))