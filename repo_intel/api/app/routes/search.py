from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import assert_repo_access, current_user
from ..db import cursor
from ..ingest.embed import embed_query
from ..vector_index import load_matrix, rrf, vector_rank

router = APIRouter(tags=["search"])


def fulltext_rank(cur, repo_id: int, q: str, k: int = 50) -> list[int]:
    cur.execute("""SELECT fn.func_id FROM functions fn JOIN files f ON f.file_id = fn.file_id
                   WHERE f.repo_id=%s
                     AND MATCH(fn.name, fn.name_tokens, fn.docstring) AGAINST (%s IN NATURAL LANGUAGE MODE)
                   ORDER BY MATCH(fn.name, fn.name_tokens, fn.docstring)
                            AGAINST (%s IN NATURAL LANGUAGE MODE) DESC
                   LIMIT %s""", (repo_id, q, q, k))
    return [r["func_id"] for r in cur.fetchall()]


def hydrate(cur, ids: list[int], vec_ids=(), ft_ids=()) -> list[dict]:
    """Load metadata for the fused ids, keep the fused order, and tag why each one matched."""
    if not ids:
        return []
    cur.execute(f"""SELECT fn.func_id, fn.name, fn.signature, fn.start_line, fn.end_line,
                           fn.snippet, f.file_path, f.language
                    FROM functions fn JOIN files f ON f.file_id = fn.file_id
                    WHERE fn.func_id IN ({','.join(['%s'] * len(ids))})""", ids)
    by_id = {r["func_id"]: r for r in cur.fetchall()}
    vs, fs = set(vec_ids), set(ft_ids)
    return [{**by_id[i],
             "matched_by": [n for n, s in (("meaning", vs), ("keyword", fs)) if i in s]}
            for i in ids if i in by_id]


@router.get("/repos/{repo_id}/search")
def search(repo_id: int, q: str = Query(..., min_length=2, max_length=200),
           k: int = Query(10, ge=1, le=50), user=Depends(current_user)):
    with cursor() as cur:
        assert_repo_access(cur, repo_id, user)
        ids, mat = load_matrix(cur, repo_id)
        vec = vector_rank(embed_query(q), ids, mat)
        ft = fulltext_rank(cur, repo_id, q)
        return hydrate(cur, rrf([vec, ft])[:k], vec, ft)


@router.get("/repos/{repo_id}/functions/{func_id}/similar")
def similar(repo_id: int, func_id: int, k: int = Query(10, ge=1, le=50),
            user=Depends(current_user)):
    with cursor() as cur:
        assert_repo_access(cur, repo_id, user)
        ids, mat = load_matrix(cur, repo_id)
        if func_id not in ids:
            raise HTTPException(404, "Function not found in this repo")
        top = vector_rank(mat[ids.index(func_id)], ids, mat, k, exclude=func_id)
        return hydrate(cur, top, vec_ids=top)