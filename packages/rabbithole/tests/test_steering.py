"""rabbitHole.steering — the litreview config-steering helpers.

Planning moved to haarpi.planner (`haarpi next` is the sole planner); what stayed in rabbitHole
is writing litreview's own numbered config so the next gather/report is aimed at the reviewer's
ask. These pin the focus-append logic that both writers share.

Runnable two ways:
    pytest tests/test_steering.py
    python tests/test_steering.py
"""

from __future__ import annotations

from rabbithole import steering


def test_append_focus_chains_onto_an_existing_line():
    class _Cfg:
        focus = "existing focus"
    cfg = _Cfg()
    steering._append_focus(cfg, "", "added")
    assert cfg.focus == "existing focus; added"


def test_append_focus_handles_an_empty_focus():
    class _Cfg:
        focus = ""
    cfg = _Cfg()
    steering._append_focus(cfg, "only")
    assert cfg.focus == "only"


def test_append_focus_skips_empty_additions():
    class _Cfg:
        focus = "base"
    cfg = _Cfg()
    steering._append_focus(cfg, "", None or "", "kept")
    assert cfg.focus == "base; kept"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    raise SystemExit(1 if failures else 0)
