"""
the google product taxonomy with 5595 paths and how answers get mapped onto it
  top      match a model answer to a real top level category ignoring case
  subtree  list every real path under one top level branch
  snap     map any model written path to the nearest real one
"""

import re
from functools import lru_cache
from pathlib import Path

_CATS = [line.strip() for line in (Path(__file__).parent.parent / "categories.txt")
         .read_text().splitlines() if line.strip() and not line.startswith("#")]


# split into lowercase stemmed tokens
def _toks(text: str) -> set[str]:
    out = set()
    for t in re.findall(r"[a-z0-9]+", text.lower()):
        if len(t) > 2:
            out.add(t[:-1] if len(t) > 3 and t.endswith("s") else t)
    return out


_PATHS = [(p, _toks(p), _toks(p.rsplit(">", 1)[-1])) for p in _CATS]


# load the local embedding model over all 5595 paths once
@lru_cache(maxsize=1)
def _embedder():
    import os
    if os.environ.get("PLUCK_EMBED") == "0":  # too heavy for tiny prod machines
        return None
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


_SET = set(_CATS)
TOPS = sorted({c.split(" > ")[0] for c in _CATS})


# match a model answer to a real top level category ignoring case
def top(name) -> str | None:
    n = str(name or "").replace("&amp;", "&").strip().lower()
    return next((t for t in TOPS if t.lower() == n), None)


# list every real path under one top level branch
def subtree(top_name: str) -> list[str]:
    return [c for c in _CATS if c == top_name or c.startswith(top_name + " > ")]


# map any model written path to a real one
# try exact match then best leaf overlap then embeddings then deepest valid prefix
def snap(path) -> str | None:
    p = re.sub(r"\s*>\s*", " > ", str(path or "").replace("&amp;", "&").strip())
    if not p or p in _SET:
        return p or None
    segs = p.split(" > ")
    # the leaf is the signal so find the real path whose leaf matches it best
    qleaf, qall = _toks(segs[-1]), _toks(p)
    scored = max(_PATHS, key=lambda t: 3 * len(qleaf & t[2]) + len(qall & t[1]))
    if qleaf & scored[2]:
        return scored[0]
    if emb := _embedder():  # synonym leaves like fragrances vs perfume need vectors
        model, mat, np = emb
        v = np.array(list(model.embed([p])))[0]
        sims = mat @ v / (np.linalg.norm(mat, axis=1) * np.linalg.norm(v) + 1e-9)
        return _CATS[int(np.argmax(sims))]
    for i in range(len(segs) - 1, 1, -1):  # else deepest valid prefix
        if (q := " > ".join(segs[:i])) in _SET:
            return q
    return scored[0] if qall & scored[1] else None
