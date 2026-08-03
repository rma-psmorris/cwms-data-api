#  MIT License
#  Copyright (c) 2026 Hydrologic Engineering Center
#  Permission is hereby granted, free of charge, to any person obtaining a copy
#  of this software and associated documentation files (the "Software"), to deal
#  in the Software without restriction, including without limitation the rights
#  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#  copies of the Software, and to permit persons to whom the Software is
#  furnished to do so, subject to the following conditions:
#  The above copyright notice and this permission notice shall be included in all
#  copies or substantial portions of the Software.
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
#  SOFTWARE.
import pytest

import utils.log_util as log_util
import utils.threading_util as threading_util


def test_a_hard_failure_raises_after_the_batch(caplog):
    """
    A staging failure used to log a warning and let the run exit 0. The publish
    phase would then either fail confusingly or republish stale data.
    """
    threading_util.init_executor(2)

    def task(item):
        if item == "bad":
            raise OSError(22, "Invalid argument")
        return None

    with pytest.raises(threading_util.TaskExecutionError, match="1 task\\(s\\) failed"):
        threading_util.execute_tasks(task, ["ok", "bad", "also ok"])


def test_every_item_is_attempted_before_raising():
    threading_util.init_executor(2)
    seen = []

    def task(item):
        seen.append(item)
        raise OSError(22, "Invalid argument")

    with pytest.raises(threading_util.TaskExecutionError):
        threading_util.execute_tasks(task, ["a", "b", "c"])

    assert sorted(seen) == ["a", "b", "c"]


def test_the_error_names_the_failing_items():
    threading_util.init_executor(2)

    def task(item):
        raise OSError(22, f"Invalid argument: {item}")

    with pytest.raises(threading_util.TaskExecutionError) as excinfo:
        threading_util.execute_tasks(task, [["SWT", "LOCATION LEVEL ASSOCIATION"]])

    assert "SWT, LOCATION LEVEL ASSOCIATION" in str(excinfo.value)


def test_many_failures_are_summarised():
    threading_util.init_executor(4)

    def task(item):
        raise ValueError(f"boom {item}")

    with pytest.raises(threading_util.TaskExecutionError) as excinfo:
        threading_util.execute_tasks(task, list(range(9)))

    message = str(excinfo.value)
    assert "9 task(s) failed" in message
    assert "and 4 more" in message


def test_missing_staged_data_is_still_only_a_warning(caplog):
    """
    FileNotFoundError is the modelled "this item was not staged" case, not a
    hard error, so it must not fail the run.
    """
    import logging
    # DEBUG: the per-item skip is no longer a warning of its own. The extract
    # phase already reported every id it found nothing for, so warning again per
    # item said the same thing a second time - and the batch's own account, which
    # the caller logs via log_util.outcome, is the line that carries the count.
    caplog.set_level(logging.DEBUG)
    threading_util.init_executor(2)

    def task(item):
        raise FileNotFoundError("No staged timeseries data found")

    result = threading_util.execute_tasks(task, ["one"])

    # The exception text is stated once; the framing adds only "Skipped '<item>'".
    assert "Skipped 'one': No staged timeseries data found" in caplog.text
    assert result.skipped == 1
    assert result.succeeded == 0


def test_a_skip_is_not_a_warning(caplog):
    """
    A skip is reported by the caller's batch line, at the level that line chooses.
    Warning here as well put two lines about the same count next to each other.
    """
    import logging
    caplog.set_level(logging.DEBUG)
    threading_util.init_executor(2)

    def task(item):
        raise FileNotFoundError("No staged data found")

    threading_util.execute_tasks(task, ["one"])

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_skip_is_recorded_on_the_tally_under_its_label(caplog):
    threading_util.init_executor(2)
    tally = log_util.Tally()

    def task(item):
        raise FileNotFoundError("No staged data found")

    threading_util.execute_tasks(
        task,
        [["SWT", "EUFA.Elev.Inst.1Hour.0.Ccp-Rev"]],
        label=lambda item: item[1],
        tally=tally,
    )

    assert tally.count(log_util.NOTHING_STAGED) == 1
    assert tally.labels(log_util.NOTHING_STAGED) == ["EUFA.Elev.Inst.1Hour.0.Ccp-Rev"]


def test_a_label_replaces_the_internal_work_item_shape(caplog):
    """
    Without a label the log falls back to comma-joining the work item, which put
    the shape of a private data structure into the output - and in a different
    format from the identifier the same item was announced under.
    """
    import logging
    caplog.set_level(logging.DEBUG)
    threading_util.init_executor(2)

    def task(item):
        raise FileNotFoundError("No staged data found")

    threading_util.execute_tasks(
        task,
        [["SWT", "EUFA.Opening.Inst.0.0.MANUAL", "2026-06-01", "2026-08-03"]],
        label=lambda item: f"{item[1]} [{item[2]} to {item[3]}]",
    )

    skip_line = next(r.getMessage() for r in caplog.records if "Skipped '" in r.getMessage())

    assert "EUFA.Opening.Inst.0.0.MANUAL [2026-06-01 to 2026-08-03]" in skip_line
    assert "SWT, EUFA.Opening" not in skip_line


def test_a_clean_batch_raises_nothing():
    threading_util.init_executor(2)

    threading_util.execute_tasks(lambda item: None, ["a", "b"])


def test_a_skip_message_does_not_repeat_the_item(caplog):
    """
    The item identifiers are rendered once, so the exception text must not restate
    them. The original line said the office, id and window twice, and "skipped"
    three times.
    """
    import logging
    caplog.set_level(logging.DEBUG)
    threading_util.init_executor(2)

    def task(item):
        raise FileNotFoundError("No staged timeseries data found for this window.")

    threading_util.execute_tasks(
        task, [["SWT", "EUFA.Precip-Alt.Total.1Day.1Day.Decodes-Raw", "2026-06-01", "2026-07-30"]]
    )

    skip_line = next(r.getMessage() for r in caplog.records if "Skipped '" in r.getMessage())

    assert skip_line.count("EUFA.Precip-Alt.Total.1Day.1Day.Decodes-Raw") == 1
    assert skip_line.count("SWT") == 1
    assert skip_line.lower().count("skipped") == 1
    assert "Run staging first" not in skip_line


def test_the_advice_appears_once_per_batch(caplog):
    """
    The advice moved onto the caller's batch line - it used to be a warning of its
    own next to a line reporting the same count - but it must still appear exactly
    once for a batch, not once per item.
    """
    import logging
    caplog.set_level(logging.WARNING)
    threading_util.init_executor(4)
    tally = log_util.Tally()

    def task(item):
        raise FileNotFoundError("No staged timeseries data found for this window.")

    threading_util.execute_tasks(task, [["SWT", f"ID{i}"] for i in range(6)], tally=tally)
    log_util.outcome(
        logging.getLogger("timeseries"),
        action="Published",
        noun="timeseries",
        total=6,
        tally=tally,
        office_id="SWT",
    )

    advice = [r.getMessage() for r in caplog.records if "run the extract phase" in r.getMessage()]

    assert len(advice) == 1
    assert "Published 0 of 6 timeseries for SWT" in advice[0]
    assert "6 with nothing staged" in advice[0]


def test_the_batch_summary_reports_the_true_proportion(caplog):
    import logging
    caplog.set_level(logging.WARNING)
    threading_util.init_executor(4)
    tally = log_util.Tally()

    def task(item):
        if item[1] in ("ID0", "ID1"):
            raise FileNotFoundError("No staged timeseries data found for this window.")
        return None

    result = threading_util.execute_tasks(task, [["SWT", f"ID{i}"] for i in range(5)], tally=tally)
    log_util.outcome(
        logging.getLogger("timeseries"),
        action="Published",
        noun="timeseries",
        total=5,
        tally=tally,
        office_id="SWT",
    )

    assert result.skipped == 2
    assert result.succeeded == 3

    summary = next(r.getMessage() for r in caplog.records if "run the extract phase" in r.getMessage())

    assert "Published 3 of 5 timeseries for SWT" in summary
    assert "2 with nothing staged" in summary


def test_the_batch_line_stays_at_info_when_nothing_was_skipped(caplog):
    """
    A clean batch is not a warning. The level rises only for the one tallied
    outcome that might mean something is wrong.
    """
    import logging
    caplog.set_level(logging.DEBUG)
    threading_util.init_executor(2)
    tally = log_util.Tally()

    threading_util.execute_tasks(lambda item: None, [["SWT", "ID0"]], tally=tally)
    log_util.outcome(
        logging.getLogger("timeseries"),
        action="Published",
        noun="timeseries",
        total=1,
        tally=tally,
        office_id="SWT",
    )

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert "Published 1 of 1 timeseries for SWT" in caplog.text
    assert "run the extract phase" not in caplog.text


def test_datetimes_are_rendered_in_the_one_display_format():
    """
    Seconds and microseconds are storage detail. The log had three renderings of
    the same instant - "2026-06-01 00.00.00", "2026-06-01 00:00:00+00:00" and
    "2026-08-03 10:47:04.197293+00:00" - and now has one, from log_util.
    """
    from datetime import datetime

    formatted = threading_util._format_item(
        ["SWT", "EUFA.Elev.Inst.1Hour.0.Ccp-Rev", datetime(2026, 7, 30, 9, 47, 52, 795232)]
    )

    assert formatted.endswith("2026-07-30 09:47")
    assert "795232" not in formatted
    assert "+00:00" not in formatted
