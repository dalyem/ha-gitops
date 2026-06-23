from engine.core.scheduler import _autopush_decision

T0 = "2026-06-20T00:00:00+00:00"
T10 = "2026-06-20T00:10:00+00:00"   # +600s
T16 = "2026-06-20T00:16:00+00:00"   # +960s


def test_first_seen_starts_timer_no_push():
    push, sig, since = _autopush_decision("A", None, None, T0, 900)
    assert push is False and sig == "A" and since == T0


def test_changed_signature_resets_timer():
    push, sig, since = _autopush_decision("B", "A", T0, T10, 900)
    assert push is False and sig == "B" and since == T10  # timer restarted at "now"


def test_stable_but_within_quiet_period_waits():
    push, sig, since = _autopush_decision("A", "A", T0, T10, 900)  # 600s < 900s
    assert push is False and since == T0


def test_stable_past_quiet_period_pushes():
    push, sig, since = _autopush_decision("A", "A", T0, T16, 900)  # 960s >= 900s
    assert push is True and since == T0
