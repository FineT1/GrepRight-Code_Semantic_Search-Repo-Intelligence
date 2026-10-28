# routes/search.py
import numpy as np
from ..ingest.embed import embed_query, MODEL_NAME

_cache: dict = {}
def invalidate(repo_id): _cache.pop((repo_id, MODEL_NAME), None)

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

def vector_rank(qvec, ids, mat, k=50, exclude=None):
    if not ids: return []
    scores = mat @ qvec                                # normalized vectors: dot = cosine
    order = np.argsort(-scores)[: k + 1]
    return [ids[i] for i in order if ids[i] != exclude][:k]

def fulltext_rank(cur, repo_id, q, k=50):
    cur.execute("""SELECT fn.func_id FROM functions fn JOIN files f ON f.file_id = fn.file_id
                   WHERE f.repo_id=%s AND MATCH(fn.name, fn.name_tokens, fn.docstring)
                         AGAINST (%s IN NATURAL LANGUAGE MODE)
                   ORDER BY MATCH(fn.name, fn.name_tokens, fn.docstring)
                            AGAINST (%s IN NATURAL LANGUAGE MODE) DESC
                   LIMIT %s""", (repo_id, q, q, k))
    return [r["func_id"] for r in cur.fetchall()]

def rrf(rankings, k=60):
    """Reciprocal Rank Fusion: merges rankings without tuning score scales."""
    scores = {}
    for ranking in rankings:
        for rank, fid in enumerate(ranking, start=1):
            scores[fid] = scores.get(fid, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=scores.get, reverse=True)

def hydrate(cur, ids, vec_ids=(), ft_ids=()):
    if not ids: return []
    cur.execute(f"""SELECT fn.func_id, fn.name, fn.signature, fn.start_line, fn.end_line,
                           fn.snippet, f.file_path, f.language
                    FROM functions fn JOIN files f ON f.file_id = fn.file_id
                    WHERE fn.func_id IN ({','.join(['%s'] * len(ids))})""", ids)
    by_id = {r["func_id"]: r for r in cur.fetchall()}
    vs, fs = set(vec_ids), set(ft_ids)
    return [{**by_id[i], "matched_by": [n for n, s in (("meaning", vs), ("keyword", fs)) if i in s]}
            for i in ids if i in by_id]

@router.get("/repos/{repo_id}/search")
def search(repo_id: int, q: str, k: int = 10, user=Depends(current_user)):
    with connect().cursor() as cur:
        ids, mat = load_matrix(cur, repo_id)
        vec = vector_rank(embed_query(q), ids, mat)
        ft = fulltext_rank(cur, repo_id, q)
        return hydrate(cur, rrf([vec, ft])[:k], vec, ft)

@router.get("/functions/{func_id}/similar")
def similar(func_id: int, repo_id: int, k: int = 10, user=Depends(current_user)):
    with connect().cursor() as cur:
        ids, mat = load_matrix(cur, repo_id)
        if func_id not in ids: raise HTTPException(404)
        top = vector_rank(mat[ids.index(func_id)], ids, mat, k, exclude=func_id)
        return hydrate(cur, top, vec_ids=top)