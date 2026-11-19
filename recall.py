"""Perfect memory: every conversation with Connor, saved on this computer and searchable by meaning.

Everything Connor says to Jarvis and what Jarvis answers goes into memory.db (SQLite, next to this
file, never uploaded). Each exchange gets:
  - a keyword index (SQLite full-text search), which always works, and
  - a meaning vector from a tiny local embedding model (potion-base-8M, about 30 MB, downloaded once
    from Hugging Face), so "that pizza place we talked about" finds "Tony's on 5th has the best slice"
    months later. It runs in milliseconds with numpy; no extra packages.

When Connor asks his brain anything, the few most relevant old conversations are added to the
prompt, and the brain also has a search_memory skill to dig further. Friends in group chat mode
are never saved. Say "forget what we said about X" to delete those conversations.
Config: "perfect_memory": false turns it off."""
import json
import re
import sqlite3
import struct
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "memory.db"
MODEL_DIR = HERE / "models" / "potion-base-8M"
MODEL_REPO = "minishlab/potion-base-8M"
_lock = threading.RLock()
_model = {"state": None}          # None = not tried, False = unavailable, else {"tok", "emb", ...}
_cache = {"ids": None, "mat": None}


def _cfg():
    try:
        return json.loads((HERE / "config.json").read_text())
    except Exception:
        return {}


def enabled():
    return _cfg().get("perfect_memory", True)


# ---------------- storage ----------------

_conn = None


def _db():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(DB), check_same_thread=False, timeout=10)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("CREATE TABLE IF NOT EXISTS talk (id INTEGER PRIMARY KEY, t REAL, user TEXT, jarvis TEXT, "
                      "source TEXT, vec BLOB)")
        try:
            _conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS talk_fts USING fts5(user, jarvis, "
                          "content='talk', content_rowid='id')")
            _conn.execute("CREATE TRIGGER IF NOT EXISTS talk_ai AFTER INSERT ON talk BEGIN INSERT INTO "
                          "talk_fts(rowid, user, jarvis) VALUES (new.id, new.user, new.jarvis); END")
            _conn.execute("CREATE TRIGGER IF NOT EXISTS talk_ad AFTER DELETE ON talk BEGIN INSERT INTO "
                          "talk_fts(talk_fts, rowid, user, jarvis) VALUES ('delete', old.id, old.user, old.jarvis); END")
        except sqlite3.OperationalError as e:
            print(f"(memory: no keyword index on this computer: {e})")
        _conn.commit()
    return _conn


def _fts_ok():
    return bool(_db().execute("SELECT name FROM sqlite_master WHERE name='talk_fts'").fetchone())


def save(user, jarvis, source="voice", t=None):
    """Remember one exchange. Skips an exact repeat of the last one."""
    if not enabled() or not (user or "").strip():
        return
    user, jarvis = user.strip()[:2000], (jarvis or "").strip()[:4000]
    try:
        with _lock:
            db = _db()
            last = db.execute("SELECT user, jarvis, t FROM talk ORDER BY id DESC LIMIT 1").fetchone()
            if last and last[0] == user and time.time() - last[2] < 90:
                if len(jarvis) > len(last[1] or ""):     # the fuller version wins
                    db.execute("UPDATE talk SET jarvis=? WHERE id=(SELECT MAX(id) FROM talk)", (jarvis,))
                    db.commit()
                return
            vec = _embed(f"{user}\n{jarvis}")
            cur = db.execute("INSERT INTO talk (t, user, jarvis, source, vec) VALUES (?,?,?,?,?)",
                             (t or time.time(), user, jarvis, source, _pack(vec)))
            db.commit()
            if vec is not None and _cache["ids"] is not None:
                import numpy as np
                _cache["ids"].append(cur.lastrowid)
                _cache["mat"] = np.vstack([_cache["mat"], vec[None, :]]) if _cache["mat"] is not None else vec[None, :]
    except Exception as e:
        print(f"(couldn't save to long-term memory: {e})")


def count():
    try:
        return _db().execute("SELECT COUNT(*) FROM talk").fetchone()[0]
    except Exception:
        return 0


# ---------------- meaning vectors (model2vec-style static embeddings, numpy only) ----------------

def _load_safetensors(path):
    import numpy as np
    raw = path.read_bytes()
    n = struct.unpack("<Q", raw[:8])[0]
    header = json.loads(raw[8:8 + n])
    kinds = {"F32": np.float32, "F16": np.float16, "F64": np.float64, "I64": np.int64, "I32": np.int32}
    out = {}
    for name, info in header.items():
        if name == "__metadata__":
            continue
        a, b = info["data_offsets"]
        out[name] = np.frombuffer(raw[8 + n + a:8 + n + b], dtype=kinds[info["dtype"]]).reshape(info["shape"])
    return out


def _download():
    try:
        if not (MODEL_DIR / "model.safetensors").exists():
            from huggingface_hub import snapshot_download
            snapshot_download(MODEL_REPO, local_dir=str(MODEL_DIR),
                              allow_patterns=["model.safetensors", "tokenizer.json", "config.json"])
        return True
    except Exception as e:
        print(f"(memory: meaning model not downloaded, using keywords: {str(e)[:150]})")
        return False


def _load_model():
    if _model["state"] is not None:
        return _model["state"] or None
    _model["state"] = False
    if not enabled() or not _download():
        return None
    try:
        import numpy as np
        from tokenizers import Tokenizer
        tensors = _load_safetensors(MODEL_DIR / "model.safetensors")
        emb = tensors.get("embeddings")
        if emb is None:
            emb = next(iter(tensors.values()))
        cfg = json.loads((MODEL_DIR / "config.json").read_text()) if (MODEL_DIR / "config.json").exists() else {}
        tok = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
        _model["state"] = {"tok": tok, "emb": emb.astype(np.float32), "weights": tensors.get("weights"),
                           "mapping": tensors.get("mapping"), "normalize": cfg.get("normalize", True),
                           "unk": tok.token_to_id("[UNK]")}
        print(f"(memory: meaning search ready, {count()} conversations remembered)")
    except Exception as e:
        print(f"(memory: meaning model failed, using keywords: {e})")
        _model["state"] = False
    return _model["state"] or None


def _embed(text):
    m = _model["state"] if _model["state"] else None
    if not m:
        return None
    import numpy as np
    ids = [i for i in m["tok"].encode(text[:2000], add_special_tokens=False).ids if i != m["unk"]]
    if not ids:
        return None
    ids = np.array(ids)
    rows = m["mapping"][ids] if m["mapping"] is not None else ids
    vecs = m["emb"][rows]
    if m["weights"] is not None:
        vecs = vecs * m["weights"][ids][:, None]
    v = vecs.mean(axis=0)
    norm = float(np.linalg.norm(v))
    return (v / norm).astype(np.float32) if norm > 0 else None


def _pack(vec):
    return None if vec is None else vec.astype("float32").tobytes()


def _matrix():
    import numpy as np
    if _cache["ids"] is None:
        rows = _db().execute("SELECT id, vec FROM talk WHERE vec IS NOT NULL").fetchall()
        _cache["ids"] = [r[0] for r in rows]
        _cache["mat"] = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows]) if rows else None
    return _cache["ids"], _cache["mat"]


def backfill():
    """Give meaning vectors to conversations saved before the model was there."""
    if not _load_model():
        return 0
    with _lock:
        db = _db()
        rows = db.execute("SELECT id, user, jarvis FROM talk WHERE vec IS NULL").fetchall()
        for rid, u, j in rows:
            db.execute("UPDATE talk SET vec=? WHERE id=?", (_pack(_embed(f"{u}\n{j}")), rid))
        db.commit()
        _cache["ids"] = None
    return len(rows)


# ---------------- searching ----------------

STOP = set("a an the and or but to of in on at for with about is are was were be been i you me my your it "
           "this that what when where who how did do does we us our".split())


def search(query, k=5, skip_recent=0.0, min_score=0.0):
    """The k past exchanges most related to query: [{"t", "when", "user", "jarvis", "score"}].
    skip_recent leaves out the last few seconds' worth (already in the recent conversation)."""
    if not enabled() or not (query or "").strip():
        return []
    scores = {}
    with _lock:
        db = _db()
        q = _embed(query) if _load_model() else None
        if q is not None:
            ids, mat = _matrix()
            if mat is not None:
                sims = mat @ q
                for i in sims.argsort()[::-1][:k * 4]:
                    scores[ids[i]] = float(sims[i])
        words = [w for w in re.findall(r"[a-z0-9']+", query.lower()) if w not in STOP and len(w) > 1]
        if words and _fts_ok():
            match = " OR ".join(f'"{w}"' for w in words[:12])
            try:
                hits = db.execute("SELECT rowid, bm25(talk_fts) FROM talk_fts WHERE talk_fts MATCH ? "
                                  "ORDER BY bm25(talk_fts) LIMIT ?", (match, k * 4)).fetchall()
            except sqlite3.OperationalError:
                hits = []
            for rid, bm in hits:                 # bm25 is negative: more negative = better
                kw = min(1.0, 0.5 + max(0.0, -bm) / 10.0)
                sem = scores.get(rid)
                if q is None:                    # no meaning model: keywords alone
                    scores[rid] = kw
                else:
                    scores[rid] = max(sem or 0.0, 0.6 * kw) + (0.1 if sem is not None and kw > 0.3 else 0.0)
        if not scores:
            return []
        cutoff = time.time() - skip_recent
        marks = ",".join("?" * len(scores))
        rows = db.execute(f"SELECT id, t, user, jarvis FROM talk WHERE id IN ({marks})", list(scores)).fetchall()
    out = [{"id": r[0], "t": r[1], "when": time.strftime("%A %B %d %Y, %I:%M %p", time.localtime(r[1])),
            "user": r[2], "jarvis": r[3], "score": round(scores[r[0]], 3)}
           for r in rows if r[1] < cutoff and scores[r[0]] >= min_score]
    return sorted(out, key=lambda r: r["score"], reverse=True)[:k]


def for_prompt(question, k=3):
    """Old conversations worth reminding the brain of, as text ('' if none fit)."""
    try:
        hits = search(question, k=k, skip_recent=900, min_score=float(_cfg().get("memory_min_score", 0.45)))
    except Exception as e:
        print(f"(memory search failed: {e})")
        return ""
    if not hits:
        return ""
    lines = [f"- {h['when']}: User said: {h['user'][:300]} | You said: {h['jarvis'][:300]}" for h in hits]
    return ("From your long-term memory, older conversations that might be relevant (use them only if they "
            "help, and mention when it was if that's useful):\n" + "\n".join(lines))


def forget(about):
    """Deletes the conversations that best match about. Returns how many."""
    hits = search(about, k=10, min_score=0.5)
    if not hits:
        return 0
    with _lock:
        db = _db()
        db.executemany("DELETE FROM talk WHERE id=?", [(h["id"],) for h in hits])
        db.commit()
        _cache["ids"] = None
    return len(hits)


def start():
    """In the background: load the meaning model, give old conversations vectors, and bring in the
    recent history from memory.json the first time."""
    def go():
        try:
            if count() == 0:
                import memory
                for h in memory.history(30):
                    try:
                        t = time.mktime(time.strptime(h.get("t", ""), "%Y-%m-%d %H:%M"))
                    except ValueError:
                        t = None
                    save(h.get("user", ""), h.get("jarvis", ""), "history", t)
            _load_model()
            n = backfill()
            if n:
                print(f"(memory: indexed {n} older conversations by meaning)")
        except Exception as e:
            print(f"(memory start-up failed: {e})")
    if enabled():
        threading.Thread(target=go, daemon=True, name="recall").start()
