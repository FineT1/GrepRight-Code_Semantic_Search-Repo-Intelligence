import numpy as np

from .ingest.embed import MODEL_NAME

VECTOR_MIN_SCORE = 0.0        # tune from your benchmark, don't guess
_cache: dict = {}


def invalidate(repo_id):
    _cache.pop((repo_id, MODEL_NAME), None)


def load_matrix(cur, repo_id):
    key = (repo_id, MODEL_NAME)
    if key not in _cache:
        cur.execute("""SELECT e.func_id, e.vector FROM function_embeddings e
                       JOIN functions fn ON fn.func_id = e.func_id
                       JOIN files f ON f.file_id = fn.file_id
                       WHERE f.repo_id=%s AND e.model_name=%s""", (repo_id, MODEL_NAME))
        rows = cur.fetchall()
        ids = [r["func_id"] for r in rows]
        mat = (np.vstack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
               if rows else np.zeros((0, 1), np.float32))
        _cache[key] = (ids, mat)
    return _cache[key]


def vector_rank(qvec, ids, mat, k=50, exclude=None, min_score=VECTOR_MIN_SCORE):
    if not ids:
        return []
    scores = mat @ qvec
    order = np.argsort(-scores)[: k + 1]
    return [ids[i] for i in order if ids[i] != exclude and scores[i] >= min_score][:k]


def rrf(rankings, k=60):
    scores = {}
    for ranking in rankings:
        for rank, fid in enumerate(ranking, start=1):
            scores[fid] = scores.get(fid, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=scores.get, reverse=True)