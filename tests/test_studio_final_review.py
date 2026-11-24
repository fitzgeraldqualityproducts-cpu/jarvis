"""Final-cut review: Claude looks at a contact sheet of the finished Short before upload."""
import json

from test_studio_foundation import studio_home  # noqa: F401
from test_studio_script import fakes, FakeClaude  # noqa: F401
from test_studio_toolchain import fake_voice  # noqa: F401


def _with_review(monkeypatch, answer, seen):
    from studio import claude, finalreview
    monkeypatch.setattr(finalreview, "mode", finalreview._config_mode)
    base = claude.RUNNER

    def runner(prompt, timeout, web):
        if prompt.startswith("You are the final editor"):
            seen.append(prompt)
            return json.dumps(answer)
        return base(prompt, timeout, web)
    return runner


def test_moments_are_the_first_frame_and_the_middle_of_each_line():
    from studio import finalreview
    assert finalreview.moments({"line_starts": [0.2, 3.2, 5.2, 9.0]}, 9.5) == [(0.0, None), (2.0, 0), (4.4, 1), (7.48, 2)]
    many = finalreview.moments({"line_starts": [float(i) for i in range(20)]}, 20)
    assert len(many) == finalreview.MAX_FRAMES and many[0] == (0.0, None)


def test_advise_mode_saves_problems_without_stopping_the_short(fakes, fake_voice, monkeypatch):  # noqa: F811
    from studio import claude, db, lessons
    from test_studio_video import _make
    seen = []
    answer = {"score": 7, "first_frame": 8, "problems": [{"frame": 3, "issue": "caption covers the diagram",
                                                          "severity": "major"}]}
    runner = _with_review(monkeypatch, answer, seen)
    real_make_runner = claude.RUNNER
    monkeypatch.setattr(claude, "RUNNER", runner)
    vid, states = _make(fakes, fake_voice, monkeypatch)
    assert states[-1] == "ready", db.video(vid)["error"]
    assert len(seen) == 1 and "the first frame, before anything is said" in seen[0] and "look/" in seen[0]
    assert 'the narrator is saying "' in seen[0]
    row = db.one("SELECT * FROM qa_checks WHERE video_id=? AND check_name='final-cut review'", vid)
    assert row["passed"] == 1 and "major: frame 3: caption covers the diagram" in row["detail"]
    fr = json.loads(db.video(vid)["meta"])["final_review"]
    assert fr["major"] == 1 and fr["mode"] == "advise" and fr["score"] == 7.0
    assert any("final review: caption covers the diagram" in str(r["detail"]) for r in db.q("SELECT * FROM lessons"))
    assert lessons is not None and real_make_runner is not None


def test_block_mode_stops_a_short_with_a_major_problem(fakes, fake_voice, monkeypatch):  # noqa: F811
    from studio import claude, config, db
    from test_studio_video import _make
    config.set_values(final_review="block")
    answer = {"score": 3, "first_frame": 4, "problems": [{"frame": 1, "issue": "garbled text in the picture",
                                                          "severity": "major"}]}
    monkeypatch.setattr(claude, "RUNNER", _with_review(monkeypatch, answer, []))
    vid, states = _make(fakes, fake_voice, monkeypatch)
    assert states[-1] != "ready" and "final-cut review" in (db.video(vid)["error"] or "")


def test_minor_problems_pass_even_in_block_mode(fakes, fake_voice, monkeypatch):  # noqa: F811
    from studio import claude, config, db
    from test_studio_video import _make
    config.set_values(final_review="block")
    answer = {"score": 9, "first_frame": 9, "problems": [{"frame": 2, "issue": "slightly dark", "severity": "minor"}]}
    monkeypatch.setattr(claude, "RUNNER", _with_review(monkeypatch, answer, []))
    vid, states = _make(fakes, fake_voice, monkeypatch)
    assert states[-1] == "ready", db.video(vid)["error"]
