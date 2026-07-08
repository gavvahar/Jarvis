def cosine_similarity(a: list, b: list) -> float:
    import numpy as np

    av = np.array(a, dtype=float)
    bv = np.array(b, dtype=float)
    denom = np.linalg.norm(av) * np.linalg.norm(bv)
    return float(np.dot(av, bv) / denom) if denom > 0 else 0.0


def best_match(embedding: list, cache: dict) -> tuple:
    """Find the closest entry in an embedding cache.

    `cache` maps id -> (stored_embedding, *metadata). Returns
    (id, similarity, metadata) for the best match, or (None, 0.0, ()) if
    the cache is empty.
    """
    best_id, best_score, best_meta = None, 0.0, ()
    for key, (stored, *meta) in cache.items():
        score = cosine_similarity(embedding, stored)
        if score > best_score:
            best_id, best_score, best_meta = key, score, meta
    return best_id, best_score, best_meta


def average_embedding(embeddings: list) -> list:
    import numpy as np

    return np.mean([np.array(e) for e in embeddings], axis=0).tolist()
