import hashlib

from fastapi import APIRouter, Depends, Query

from ..auth import assert_repo_access, current_user
from ..db import cursor

router = APIRouter(tags=["stats"])


def mask_email(email: str) -> str:
    """Never send raw author emails to the browser."""
    return "contributor-" + hashlib.sha256((email or "").encode()).hexdigest()[:8]


def risk_score(total_files: int, at_risk: int, active_90d: int, days_since_last: int) -> dict:
    """Transparent score: every factor is returned so the UI can explain it."""
    ownership = at_risk / total_files if total_files else 0.0
    activity = 0.0 if active_90d >= 3 else (0.5 if active_90d >= 1 else 1.0)
    staleness = min(days_since_last / 365, 1.0)
    return {
        "score": round(100 * (0.5 * ownership + 0.25 * activity + 0.25 * staleness)),
        "factors": {"single_owner_files": round(ownership, 2),
                    "low_recent_activity": activity,
                    "staleness": round(staleness, 2)},
    }


@router.get("/repos/{repo_id}/stats/summary")
def summary(repo_id: int, user=Depends(current_user)):
    with cursor() as cur:
        assert_repo_access(cur, repo_id, user)
        cur.execute("""SELECT COUNT(*) AS commits,
                              COUNT(DISTINCT author_email) AS contributors,
                              DATEDIFF(NOW(), MAX(committed_at)) AS days_since_last
                       FROM commits WHERE repo_id=%s""", (repo_id,))
        s = cur.fetchone()
        cur.execute("""SELECT COUNT(DISTINCT author_email) AS n FROM commits
                       WHERE repo_id=%s AND committed_at >= NOW() - INTERVAL 90 DAY""", (repo_id,))
        active = cur.fetchone()["n"]
        cur.execute("""SELECT COUNT(*) AS total,
                              COALESCE(SUM(top_share >= 0.8 AND total_changes >= 5), 0) AS at_risk
                       FROM v_file_ownership WHERE repo_id=%s""", (repo_id,))
        o = cur.fetchone()
    at_risk = int(o["at_risk"])
    return {**s, "active_90d": active, "files_at_risk": at_risk,
            "risk": risk_score(o["total"], at_risk, active, s["days_since_last"] or 0)}


@router.get("/repos/{repo_id}/stats/hotspots")
def hotspots(repo_id: int, limit: int = Query(20, ge=1, le=100), user=Depends(current_user)):
    with cursor() as cur:
        assert_repo_access(cur, repo_id, user)
        cur.execute("""SELECT file_path, language, line_count, changes, churn, authors,
                              changes_90d, hotspot_score
                       FROM v_file_hotspots WHERE repo_id=%s
                       ORDER BY hotspot_score DESC LIMIT %s""", (repo_id, limit))
        return cur.fetchall()


@router.get("/repos/{repo_id}/stats/contributors")
def contributors(repo_id: int, user=Depends(current_user)):
    with cursor() as cur:
        assert_repo_access(cur, repo_id, user)
        cur.execute("SELECT * FROM v_contributor_stats WHERE repo_id=%s ORDER BY commits DESC LIMIT 20",
                    (repo_id,))
        rows = cur.fetchall()
    return [{"contributor": mask_email(r["author_email"]), "name": r["author_name"],
             "commits": r["commits"], "additions": r["additions"], "deletions": r["deletions"],
             "first_commit": r["first_commit"], "last_commit": r["last_commit"]} for r in rows]


@router.get("/repos/{repo_id}/stats/languages")
def languages(repo_id: int, user=Depends(current_user)):
    with cursor() as cur:
        assert_repo_access(cur, repo_id, user)
        cur.execute("""SELECT COALESCE(language, 'Other') AS language, COUNT(*) AS files,
                              COALESCE(SUM(line_count), 0) AS line_total
                       FROM files WHERE repo_id=%s AND exists_at_head=1
                       GROUP BY language ORDER BY line_total DESC""", (repo_id,))
        return cur.fetchall()


@router.get("/repos/{repo_id}/stats/at-risk-files")
def at_risk_files(repo_id: int, user=Depends(current_user)):
    with cursor() as cur:
        assert_repo_access(cur, repo_id, user)
        cur.execute("""SELECT file_path, top_author, total_changes, top_share FROM v_file_ownership
                       WHERE repo_id=%s AND top_share >= 0.8 AND total_changes >= 5
                       ORDER BY total_changes DESC LIMIT 20""", (repo_id,))
        rows = cur.fetchall()
    return [{**r, "top_author": mask_email(r["top_author"])} for r in rows]