"""Answer memory: a local vector store (Chroma) of Claude's answers to timeless questions, so asking the
same thing again ("what's a J-term?", "what is dark matter?") is answered from memory instead of a new
Claude call.

Strict on purpose, because a wrong cached answer is worse than a Claude call:
  - only general-knowledge questions with no time words (today, latest, news, price, weather ...), nothing
    personal (my, me, you, his projects) and nothing that needed a web search for current info
  - only answers that are complete, not errors, not cut off and not a question back
  - a match needs very close meaning (Chroma search) AND the same key words; a match worded differently
    ("how far is the moon" / "distance to the moon") has to be confirmed "same question" by the local
    model (local_brain.py), else Claude answers
  - entries expire after 30 days, and only answers given in the same personality are reused

Meaning vectors come from the small embedding model Jarvis already has for his memory (recall.py,
potion-base-8M), so nothing new is downloaded. Chroma (pip chromadb, installed by "Install Local Brain")
stores and searches them; without Chroma the same thing runs on a small numpy table in SQLite.
config.json "local_brain": {"answer_cache": false} turns it off."""
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("JARVIS_LOCAL_BRAIN_DATA") or HERE / "local_brain_data")
MAX_AGE_DAYS = 30
MATCH = 0.90          # meaning match needed to consider a stored answer at all
SURE = 0.95           # with the same key words and at least this close, no local double-check is needed

_lock = threading.Lock()
_stores = {}


def _embed(text):
    import recall
    if not recall._load_model():
        return None
    return recall._embed(text)


EMBED = _embed        # tests swap this


class Store:
    """A small vector collection: Chroma when it's installed, else numpy over SQLite. Cosine similarity."""

    def __init__(self, name, path=None):
        self.name = name
        self.path = Path(path or DATA)
        self.kind = None
        self._col = None
        self._db = None

    def _open(self):
        if self.kind:
            return
        self.path.mkdir(parents=True, exist_ok=True)
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(self.path / "chroma"))
            self._col = client.get_or_create_collection(self.name, metadata={"hnsw:space": "cosine"})
            self.kind = "chroma"
            return
        except Exception as e:
            if not isinstance(e, ImportError):
                print(f"(answer memory: Chroma failed, using the built-in table: {type(e).__name__}: {str(e)[:120]})")
        self._db = sqlite3.connect(str(self.path / "vectors.db"), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS vec(coll TEXT, id TEXT, doc TEXT, meta TEXT, v BLOB, "
                         "PRIMARY KEY(coll, id))")
        self._db.commit()
        self.kind = "table"

    def add(self, id, vec, doc, meta):
        import numpy as np
        with _lock:
            self._open()
            vec = np.asarray(vec, dtype=np.float32)
            if self.kind == "chroma":
                self._col.upsert(ids=[id], embeddings=[vec.tolist()], documents=[doc], metadatas=[meta])
            else:
                self._db.execute("INSERT OR REPLACE INTO vec VALUES(?,?,?,?,?)",
                                 (self.name, id, doc, json.dumps(meta), vec.tobytes()))
                self._db.commit()

    def query(self, vec, k=3):
        """[(similarity 0-1, id, doc, meta)] best first."""
        import numpy as np
        with _lock:
            self._open()
            vec = np.asarray(vec, dtype=np.float32)
            if self.kind == "chroma":
                if not self._col.count():
                    return []
                r = self._col.query(query_embeddings=[vec.tolist()], n_results=min(k, self._col.count()))
                return [(1 - float(d), i, doc, m or {}) for d, i, doc, m in
                        zip(r["distances"][0], r["ids"][0], r["documents"][0], r["metadatas"][0])]
            rows = self._db.execute("SELECT id, doc, meta, v FROM vec WHERE coll=?", (self.name,)).fetchall()
        if not rows:
            return []
        mat = np.vstack([np.frombuffer(r[3], dtype=np.float32) for r in rows])
        norms = np.linalg.norm(mat, axis=1) * (np.linalg.norm(vec) or 1.0)
        sims = mat @ vec / np.where(norms == 0, 1.0, norms)
        best = np.argsort(-sims)[:k]
        return [(float(sims[i]), rows[i][0], rows[i][1], json.loads(rows[i][2])) for i in best]

    def delete(self, ids):
        if not ids:
            return
        with _lock:
            self._open()
            if self.kind == "chroma":
                self._col.delete(ids=list(ids))
            else:
                self._db.executemany("DELETE FROM vec WHERE coll=? AND id=?", [(self.name, i) for i in ids])
                self._db.commit()

    def count(self):
        with _lock:
            self._open()
            if self.kind == "chroma":
                return self._col.count()
            return self._db.execute("SELECT COUNT(*) FROM vec WHERE coll=?", (self.name,)).fetchone()[0]


def store(name="answers"):
    if name not in _stores:
        _stores[name] = Store(name)
    return _stores[name]


# ---------------- which questions and answers may be remembered ----------------

TIMELY = re.compile(
    r"\b(?:today|tonight|tomorrow|yesterday|now|right now|currently|current|latest|recent|recently|new|newest|"
    r"this (?:week|month|year|season|morning|evening)|next|last|news|score|scores|price|prices|cost|stock|"
    r"weather|forecast|temperature|open|opening|hours|traffic|election|release|released|update|version|"
    r"20\d\d|ago|still|yet|anymore|live|happening|trending|game|match|playing|president|ceo|owner|champion|"
    r"champions|record|richest|best|top|population|rank|ranked|leader|winner|won)\b", re.I)
PERSONAL = re.compile(
    r"\b(?:i|i'm|im|i've|me|my|mine|we|our|us|you|your|you're|yours|jarvis|connor|he|she|they|him|her|them|"
    r"this|that|these|those|it|here|there|store ?forge|team|clipper|studio|channel|stream|twitch|discord|"
    r"spotify|obsidian|calendar|project|file|code)\b", re.I)
KNOWLEDGE = re.compile(r"^(?:so |and |um |uh |hey |yo )?(?:what(?:'s| is| are| does| do| was| were)|who (?:is|was|were|invented|discovered|wrote)|"
                       r"why (?:is|are|do|does|did)|how (?:does|do|is|are|did|many|much|far|big|long|old)|"
                       r"explain|define|tell me about|what's the (?:difference|meaning)|whats|meaning of)\b", re.I)
BAD_ANSWER = re.compile(r"hit your .*limit|my brain|can't find claude|log in|taking me too long|i'm sorry|sorry|"
                        r"i can't|i don't know|not sure|\(cut off\)|did you mean", re.I)
STOP = set("a an the of to in on for is are was were be do does did whats "
           "tell me about explain define please so and um uh hey yo jarvis bro mean meaning really actually "
           "kind sort like just quick quickly".split())          # question words (what, why, how) do count


def cacheable(question):
    q = (question or "").strip()
    if not q or len(q) > 160 or not KNOWLEDGE.search(q):
        return False
    return not TIMELY.search(q) and not PERSONAL.search(q)


def good_answer(answer):
    a = (answer or "").strip()
    return 20 <= len(a) <= 900 and not a.endswith("?") and not BAD_ANSWER.search(a)


def keywords(text):
    words = re.findall(r"[a-z0-9]+", (text or "").lower().replace("'s", ""))
    out = set()
    for w in words:
        if w in STOP:
            continue
        out.add(re.sub(r"(?:ies|es|s)$", "", w) if len(w) > 4 else w)
    return out


def _persona_key():
    try:
        import personality
        return personality.current()
    except Exception:
        return ""


def enabled():
    try:
        import local_brain
        return local_brain.enabled("answer_cache")
    except Exception:
        return False


# ---------------- using it ----------------

def remember(question, answer):
    """Called after Claude answers. Stores it only if it's safe to reuse."""
    if not enabled() or not cacheable(question) or not good_answer(answer):
        return False
    vec = EMBED(question)
    if vec is None:
        return False
    key = hashlib.sha1(" ".join(sorted(keywords(question))).encode()).hexdigest()[:16]
    store().add(key, vec, answer.strip(), {"q": question.strip()[:200], "t": time.time(), "persona": _persona_key()})
    return True


def _same_question(a, b):
    """Asks the local model whether two questions ask exactly the same thing. None if it can't tell."""
    try:
        import local_brain
        if not local_brain.ready()[0]:
            return None
        ans = local_brain.chat_json([
            {"role": "system", "content": "You compare two questions. Answer JSON only: {\"same\": true or false}. "
                                          "true only if one answer fully and correctly answers both."},
            {"role": "user", "content": f"Question A: {a}\nQuestion B: {b}"}], max_tokens=12, timeout=4, first_timeout=3,
            temperature=0)
        return None if ans is None else bool(ans.get("same"))
    except Exception:
        return None


def lookup(question):
    """A stored answer to this same question, or None (then Claude answers as usual)."""
    if not enabled() or not cacheable(question):
        return None
    vec = EMBED(question)
    if vec is None:
        return None
    try:
        hits = store().query(vec, k=3)
    except Exception as e:
        print(f"(answer memory failed: {e})")
        return None
    words = keywords(question)
    old = []
    for sim, id, doc, meta in hits:
        if time.time() - float(meta.get("t", 0)) > MAX_AGE_DAYS * 86400:
            old.append(id)
            continue
        if sim < MATCH or meta.get("persona", "") != _persona_key():
            continue
        same_words = keywords(meta.get("q", "")) == words
        if not (same_words and sim >= SURE) and _same_question(question, meta.get("q", "")) is not True:
            continue
        try:
            import local_brain
            local_brain.saved("cache")
        except Exception:
            pass
        print(f"(answer memory: reused the answer to {meta.get('q')!r}, similarity {sim:.3f}, no Claude call)")
        if old:
            store().delete(old)
        return doc
    if old:
        store().delete(old)
    return None
