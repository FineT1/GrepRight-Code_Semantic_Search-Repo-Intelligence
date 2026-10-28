# routes/stats.py (core logic)
import hashlib

def mask_email(e: str) -> str:                    # never expose raw emails in the UI
    return "contributor-" + hashlib.sha256(e.encode()).hexdigest()[:8]

def risk_score(total_files, at_risk, active_90d, days_since_last):
    """Transparent score: every factor is returned so the UI can explain it."""
    ownership = at_risk / total_files if total_files else 0.0
    activity  = 0.0 if active_90d >= 3 else (0.5 if active_90d >= 1 else 1.0)
    staleness = min(days_since_last / 365, 1.0)
    return {"score": round(100 * (0.5 * ownership + 0.25 * activity + 0.25 * staleness)),
            "factors": {"single_owner_files": round(ownership, 2),
                        "low_recent_activity": activity, "staleness": round(staleness, 2)}}

@router.get("/repos/{repo_id}/stats/summary")
def summary(repo_id: int, user=Depends(current_user)):
    with connect().cursor() as cur:
        cur.execute("""SELECT COUNT(*) AS commits, COUNT(DISTINCT author_email) AS contributors,
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
        return {**s, "active_90d": active, "files_at_risk": int(o["at_risk"]),
                "risk": risk_score(o["total"], int(o["at_risk"]), active, s["days_since_last"] or 0)}

@router.get("/repos/{repo_id}/stats/hotspots")
def hotspots(repo_id: int, limit: int = 20, user=Depends(current_user)):
    with connect().cursor() as cur:
        cur.execute("SELECT * FROM v_file_hotspots WHERE repo_id=%s ORDER BY hotspot_score DESC LIMIT %s",
                    (repo_id, limit))
        return cur.fetchall()