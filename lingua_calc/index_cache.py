"""Process-wide cache of indexes rebuilt from stored runs.

Its own module, rather than living beside ``load_run_index`` in ``pipeline``, so
that ``store`` can invalidate on delete without importing ``pipeline``, which
imports ``store``. Nothing here knows how an index is built — only which
database a cached one came from, and how long it stays true.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Sequence

from lingua_calc.corpus import CorpusIndex

# A run is immutable — `save_run` only ever appends — so an index built from one
# stays correct until the run is deleted, and the cache never needs invalidating
# except there. It earns its keep on the lemma lens: browsing a word at a time is
# a request per lemma, and each one would otherwise re-read every fact row and
# rebuild every track before answering a question about a few dozen tokens.
#
# Two entries, because the realistic pattern is reading one run while comparing
# against another; a larger cache would hold megabytes of facts for runs nobody
# has looked at since.
CACHE_SIZE = 2

Key = tuple[str, str]

_cache: "OrderedDict[Key, CorpusIndex]" = OrderedDict()
_lock = threading.Lock()


def key(db_path: str | Path, run_id: str) -> Key:
    """Cache identity: which database, and which run inside it.

    The run id alone was enough only by accident. Ids are uuid4, so two stores
    are unlikely to name the same run — but nothing says they cannot, and the
    cache would then hand back an index built from the other database. The path
    is normalized so that the same file reached relatively and absolutely is one
    entry; otherwise a delete through one spelling would leave the other's entry
    serving rows that are gone.
    """
    return (os.path.normcase(os.path.abspath(db_path)), run_id)


def get(cache_key: Key) -> CorpusIndex | None:
    """The cached index for a key, or ``None``. Marks it most-recently used."""
    with _lock:
        index = _cache.get(cache_key)
        if index is not None:
            _cache.move_to_end(cache_key)
        return index


def put(cache_key: Key, index: CorpusIndex) -> None:
    with _lock:
        _cache[cache_key] = index
        _cache.move_to_end(cache_key)
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)


def forget(db_path: str | Path, run_ids: Sequence[str]) -> None:
    """Drop cached indexes for runs that no longer exist.

    Called by ``TokenStore.delete_runs`` itself, not by its callers. Deleting is
    the one operation that can make a cached index wrong, so it is the one place
    that has to say so — and it owns that, rather than trusting every caller to
    remember. Leaving it to the two HTTP delete routes meant a run removed
    through any other path stayed readable, with facts the database no longer
    has, for the life of the process.
    """
    with _lock:
        for run_id in run_ids:
            _cache.pop(key(db_path, run_id), None)
