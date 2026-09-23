"""Shortlist Google taxonomy paths for the category question.

Lexical stemmed matching, leaf-weighted; unioned with local embeddings when
fastembed is installed (it fixes the trousers/pants class of vocabulary gap).
"""

import re
from functools import lru_cache
from pathlib import Path

_CATS = [l.strip() for l in (Path(__file__).parent.parent / "categories.txt")
         .read_text().splitlines() if l.strip() and not l.startswith("#")]


def _toks(text: str) -> set[str]:
    out = set()
    for t in re.findall(r"[a-z0-9]+", text.lower()):
        if len(t) > 2:
            out.add(t[:-1] if len(t) > 3 and t.endswith("s") else t)
    return out


_PATHS = [(p, _toks(p), _toks(p.rsplit(">", 1)[-1])) for p in _CATS]


@lru_cache(maxsize=1)
def _embedder():
    try:
        import numpy as np
        from fastembed import TextEmbedding
        model = TextEmbedding("BAAI/bge-small-en-v1.5")
        cache = Path(__file__).parent.parent / ".cache" / "tax.npy"
        if cache.exists():
            emb = np.load(cache)
        else:
            emb = np.array(list(model.embed(_CATS)))
            cache.parent.mkdir(exist_ok=True)
            np.save(cache, emb)
        return model, emb, np
    except ImportError:
        return None


def shortlist(query: str, k: int = 10) -> list[str]:
    q = _toks(query)
    scored = sorted(
        ((len(q & al) + 1.5 * len(q & lf), p) for p, al, lf in _PATHS if q & al),
        reverse=True,
    )
    lex = [p for _, p in scored[: k]]

    emb = _embedder()
    if emb:
        model, mat, np = emb
        v = np.array(list(model.embed([query])))[0]
        sims = mat @ v / (np.linalg.norm(mat, axis=1) * np.linalg.norm(v) + 1e-9)
        for i in np.argsort(-sims)[: k // 2]:
            if _CATS[i] not in lex:
                lex.append(_CATS[i])
    return lex[: k + k // 2]
