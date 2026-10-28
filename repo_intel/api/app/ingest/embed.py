import threading

import numpy as np

MODEL_NAME = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_model, _lock = None, threading.Lock()


def model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer   # heavy import: load lazily
                _model = SentenceTransformer(MODEL_NAME)
                _model.max_seq_length = 512
    return _model


def preload() -> None:
    model()


def embed_documents(texts):
    return model().encode(texts, batch_size=32, normalize_embeddings=True,
                          show_progress_bar=False).astype(np.float32)


def embed_query(q: str):
    return model().encode(QUERY_PREFIX + q, normalize_embeddings=True).astype(np.float32)


def doc_text(r) -> str:
    return (f"File: {r['file_path']}\nSignature: {r['signature'] or r['name']}\n"
            f"Docstring: {r['docstring'] or ''}\nCode:\n{r['snippet'] or ''}")


def embed_pending(cur, repo_id: int) -> None:
    """Embeds only functions without a vector for the current model; safe to re-run after a crash."""
    cur.execute("""SELECT fn.func_id, fn.name, fn.signature, fn.docstring, fn.snippet, f.file_path
                   FROM functions fn JOIN files f ON f.file_id = fn.file_id
                   LEFT JOIN function_embeddings e ON e.func_id = fn.func_id AND e.model_name = %s
                   WHERE f.repo_id = %s AND e.func_id IS NULL""", (MODEL_NAME, repo_id))
    rows = cur.fetchall()
    for i in range(0, len(rows), 256):
        chunk = rows[i:i + 256]
        vecs = embed_documents([doc_text(r) for r in chunk])
        cur.executemany(
            "INSERT IGNORE INTO function_embeddings (func_id, model_name, dim, vector) "
            "VALUES (%s,%s,%s,%s)",
            [(r["func_id"], MODEL_NAME, int(vecs.shape[1]), v.tobytes()) for r, v in zip(chunk, vecs)])