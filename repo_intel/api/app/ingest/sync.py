import tempfile

from ..db import cursor
from ..vector_index import invalidate
from .clone import clone
from .code_parser import ingest_files
from .deps import check_dependencies
from .embed import embed_pending
from .git_log import ingest_history


def process_repo(cur, repo_id: int, repo_dir: str):
    """Analyze an already-cloned directory. Tests call this directly on the fixture repo."""
    notes = [ingest_history(cur, repo_id, repo_dir),    # commits, per-file changes (transactional)
             ingest_files(cur, repo_id, repo_dir)]      # files, functions, manifests (one transaction)
    embed_pending(cur, repo_id)                         # vectors, only for new or changed functions
    check_dependencies(cur, repo_id)                    # OSV lookup, tolerates being offline
    invalidate(repo_id)                                 # drop the cached vector matrix
    return "; ".join(n for n in notes if n) or None


def run_sync(job_id: int, repo_id: int, url: str) -> None:
    with cursor() as cur:
        cur.execute("UPDATE sync_jobs SET status='running' WHERE job_id=%s", (job_id,))
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:   # Python 3.10+
                clone(url, tmp)
                note = process_repo(cur, repo_id, tmp)
            cur.execute("UPDATE repositories SET last_synced_at=NOW() WHERE repo_id=%s", (repo_id,))
            cur.execute("UPDATE sync_jobs SET status='done', error=%s, finished_at=NOW() WHERE job_id=%s",
                        (note, job_id))
        except Exception as e:
            cur.execute("UPDATE sync_jobs SET status='failed', error=%s, finished_at=NOW() WHERE job_id=%s",
                        (str(e)[:500], job_id))