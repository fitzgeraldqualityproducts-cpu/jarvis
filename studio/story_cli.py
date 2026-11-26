"""`python -m studio story <video id>`: the Narrative Integrity Engine's verdict on a Short's newest script, read
only (no Claude call: the checker's story notes saved with the script are reused)."""
import json

from studio import db, narrative


def text(video_id):
    row = db.one("SELECT * FROM scripts WHERE video_id=? ORDER BY version DESC LIMIT 1", video_id)
    if not row:
        return f"Video {video_id} has no script."
    st, ck = json.loads(row["structure"] or "{}"), json.loads(row["checks"] or "{}")
    draft = {"hooks": json.loads(row["hooks"] or "[]"), "hook": row["hook"], "lines": st.get("lines") or [],
             "calcs": st.get("calcs") or [], "model": st.get("model")}
    story = (ck.get("narrative") or {}).get("story")
    r = narrative.evaluate(draft, story)
    out = [f"Video {video_id}, script version {row['version']} ({row['status']}): {narrative.summary(r)}"
           + ("" if story else " (code checks only: this script has no saved story notes from the checker)")]
    if r["hard"]:
        out += ["Blocking problems:"] + [f"  - {h[len('narrative: '):]}" for h in r["hard"]]
    if r["advice"]:
        out += ["Notes:"] + [f"  - {a}" for a in r["advice"]]
    sf = st.get("story_fix")
    if sf:
        out.append(f"Story repair: {sf.get('edits', 0)} edit(s)"
                   + (f"; lines it didn't touch: {', '.join(map(str, sf['missed_lines']))}"
                      if sf.get("missed_lines") else ""))
    out.append("Parts: " + ", ".join(f"{k} {v:.0%}" for k, v in r["parts"].items()))
    return "\n".join(out)
