"""A queued task's duration is a budget the rest of the queue leans on.

Pins the 2026-07-16 failure: `litreview report 1` (task 591) was budgeted at the
3-hour cold-start constant while a ~26-hour synthesis ran under it, dragging every
downstream start time. Two causes, both pinned here: the June title rename orphaned
the step from its realised history ("lit review write 4" no longer matched), and
the median of a high-dispersion window undershoots exactly when it matters.
"""

from haarpi.planner import _canonical, _parse_title, _title, estimate_hours, next_cycle


class TestTitleNamesItsTool:
    """A task title names the tool the author runs, not the internal stage word, and the
    venue sits after the step: "raconteur outline css2026 9", not "paper css2026 outline 9".
    The board then reads as the work does, and a glance down the column lines up the steps."""

    def test_the_written_form_is_tool_step_venue_cycle(self):
        assert _title("paper", "outline", "css2026", 9) == "raconteur outline css2026 9"
        assert _title("litreview", "gather", "", 3) == "rabbithole gather 3"
        assert _title("experiments", "process", "", 2) == "rayleigh process 2"

    def test_a_new_title_round_trips(self):
        stage, step, venue, cycle = _parse_title("raconteur outline css2026 9")
        assert (stage, step, venue, cycle) == ("paper", "outline", "css2026", 9)

    def test_an_old_title_still_parses_so_history_survives(self):
        assert _parse_title("paper css2026 outline 9") == ("paper", "outline", "css2026", 9)
        assert _parse_title("litreview gather 3") == ("litreview", "gather", "", 3)

    def test_next_cycle_reads_a_board_of_mixed_eras(self):
        mixed = ["paper css2026 outline 7", "raconteur draft css2026 8"]
        assert next_cycle(mixed, "paper", "css2026") == 9
        assert next_cycle(mixed, "paper", "ismir") == 1     # cycles count per venue


def _done(title: str, hours: float, end: str = "2026-07-01") -> dict:
    return {"title": title, "status": "done", "duration": hours, "end_date": end}


class TestTitleErasPoolTogether:
    def test_legacy_write_history_budgets_a_new_style_report(self):
        # The write->report rename orphaned its history once; the synonym re-pools it. The
        # single-token "litreview write" form is what July wrote — the earlier two-word
        # "lit review write" is intentionally NOT recovered (its titles predate the rename
        # and were left un-renamed on the board).
        history = [_done(f"litreview write {i}", h, end=f"2026-06-{10+i:02d}")
                   for i, h in enumerate([2.42, 1.43, 1.43, 1.22, 6.19, 34.99,
                                          40.44, 16.03], start=1)]
        assert estimate_hours(history, "litreview", "report", 3.0) > 3.0

    def test_the_tool_name_pools_with_the_stage_name(self):
        # The tool->stage rename must not orphan history the way write->report did: a task
        # titled by its tool ("raconteur outline …") is the same work as one titled by its
        # stage ("paper … outline …"), whichever order the venue sits in.
        assert _canonical("raconteur outline css2026 9") == ("paper", "outline")
        assert _canonical("raconteur outline css2026 9") == _canonical("paper ismir outline 3")

    def test_a_venue_infix_folds_into_its_stage(self):
        assert _canonical("paper ismir outline 3") == ("paper", "outline")

    def test_titles_without_a_cycle_number_carry_no_identity(self):
        assert _canonical("bug fix") is None
        assert _canonical("prep lit review") is None


class TestTheBudgetIsNotAForecast:
    def test_high_dispersion_budgets_high_not_middle(self):
        # Median of this window is 16.03 — the realised run took ~26h. The
        # second-highest (34.99) covers it; the median does not.
        history = [_done(f"litreview report {i}", h, end=f"2026-06-{10+i:02d}")
                   for i, h in enumerate([1.22, 6.19, 34.99, 40.44, 16.03], start=1)]
        assert estimate_hours(history, "litreview", "report", 3.0) == 34.99

    def test_one_freak_outlier_does_not_own_the_budget(self):
        history = [_done(f"litreview gather {i}", h, end=f"2026-06-{10+i:02d}")
                   for i, h in enumerate([1.2, 1.3, 1.1, 40.0, 1.25], start=1)]
        assert estimate_hours(history, "litreview", "gather", 1.3) == 1.3

    def test_tiny_windows_budget_at_their_max(self):
        history = [_done("litreview gather 1", 2.5, end="2026-06-10"),
                   _done("litreview gather 2", 5.8, end="2026-06-11")]
        assert estimate_hours(history, "litreview", "gather", 1.3) == 5.8

    def test_no_history_still_means_the_cold_start_constant(self):
        assert estimate_hours([], "litreview", "report", 3.0) == 3.0

    def test_only_the_recent_window_counts(self):
        old = [_done(f"litreview report {i}", 100.0, end=f"2026-05-{i:02d}")
               for i in range(1, 6)]
        recent = [_done(f"litreview report {i}", 2.0, end=f"2026-06-{i:02d}")
                  for i in range(6, 11)]
        assert estimate_hours(old + recent, "litreview", "report", 3.0) == 2.0

    def test_unfinished_and_zero_duration_tasks_are_not_history(self):
        history = [
            {"title": "litreview report 1", "status": "in_progress",
             "duration": 26.0, "end_date": "2026-07-16"},
            _done("litreview report 2", 0.0),
            _done("litreview report 3", None),
        ]
        history[2]["duration"] = None
        assert estimate_hours(history, "litreview", "report", 3.0) == 3.0
