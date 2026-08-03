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
import logging
import time
from datetime import datetime
from typing import Iterable

import utils.cda_errors as cda_errors
import utils.log_util as log_util
import utils.threading_util as threading_util
import utils.filesystem_store as filesystem_store
import cwms
from config import TimeseriesConfig

logger = logging.getLogger(__name__)
DATE_TIME_FORMAT = "%Y-%m-%d %H.%M.%S"
TIMESERIES_FOLDER = "Timeseries"

# "Nothing here" is the bulk outcome, not the exception: whole association
# categories are applied to every project, so most ids have nothing for most
# projects. Collected and reported once per batch rather than a line each.
_NOT_FOUND = "not found in the source"
_EMPTY_WINDOW = "with no values in the window"
_STAGED_EMPTY = "with no staged values"

_tally = log_util.Tally()


def _start_batch() -> log_util.Tally:
    global _tally
    _tally = log_util.Tally()

    return _tally


def _label(ts_info) -> str:
    """
    A work item as the identifier it is announced under, for the skip and failure
    lines. Previously those lines carried the internal list instead
    ("SWT, EUFA.Opening.Inst.0.0.MANUAL, 2026-06-01 00:00:00, ...").
    """
    return f"{ts_info[1]} [{log_util.window(ts_info[2], ts_info[3])}]"


def _value_count(data: object) -> int | None:
    """
    Number of values in a timeseries payload, or None if it does not look like
    one. CDA answers 200 with "values": [] for an id that exists but has nothing
    in the window - distinct from the 404 it gives when there is no data at all.
    """
    if not isinstance(data, dict) or "values" not in data:
        return None

    values = data.get("values")

    return len(values) if isinstance(values, (list, tuple)) else None


def stage_timeseries(
    office_id: str,
    timeseries: Iterable[TimeseriesConfig],
    default_start: str | None,
    default_end: str | None,
) -> None:
    configured = list(timeseries)
    ts_info = _build_timeseries_work_items(office_id, configured, default_start, default_end)
    if not ts_info:
        _report_nothing_to_do(office_id, configured, "extract")
        return

    tally = _start_batch()
    started = time.monotonic()
    threading_util.execute_tasks(_download_one_ts_data, ts_info, label=_label, tally=tally)
    log_util.outcome(
        logger,
        action="Staged",
        noun="timeseries",
        total=len(ts_info),
        tally=tally,
        office_id=office_id,
        elapsed=time.monotonic() - started,
    )


def publish_staged_timeseries(
    office_id: str,
    timeseries: Iterable[TimeseriesConfig],
    default_start: str | None,
    default_end: str | None,
) -> None:
    configured = list(timeseries)
    ts_info = _build_timeseries_work_items(office_id, configured, default_start, default_end)
    if not ts_info:
        _report_nothing_to_do(office_id, configured, "load")
        return

    tally = _start_batch()
    started = time.monotonic()
    threading_util.execute_tasks(_upload_one_ts_data, ts_info, label=_label, tally=tally)
    log_util.outcome(
        logger,
        action="Published",
        noun="timeseries",
        total=len(ts_info),
        tally=tally,
        office_id=office_id,
        elapsed=time.monotonic() - started,
    )


def _report_nothing_to_do(office_id: str, configured: list, phase: str) -> None:
    """
    Nothing configured is a normal config, not a problem. Only items that were
    supplied and then all rejected are worth a warning.

    The old unconditional warning fired once per project per phase for any
    project with no timeseries, and did so directly after the project header line
    had already said so.
    """
    if not configured:
        logger.debug("No timeseries configured for office %s; nothing to %s.", office_id, phase)
        return

    logger.warning(
        "All %s configured for office %s were rejected as invalid; nothing to %s.",
        log_util.plural(len(configured), "timeseries"),
        office_id,
        phase,
    )


def _download_one_ts_data(ts_info):
    office_id = ts_info[0]
    ts_id = ts_info[1]
    begin = ts_info[2]
    end = ts_info[3]
    begin_str = begin.strftime(DATE_TIME_FORMAT)
    end_str = end.strftime(DATE_TIME_FORMAT)
    # DEBUG: with a whole association category applied to every project this is
    # one line per item before anything has happened, and the batch reports what
    # actually happened when it knows.
    logger.debug("Extracting timeseries %s for office %s [%s]", ts_id, office_id, log_util.window(begin, end))

    try:
        data = cwms.get_timeseries(ts_id, office_id, begin=begin, end=end).json
    except Exception as error:
        if not cda_errors.is_no_data(error):
            raise

        # Nothing is staged, so a later publish of this window finds no file and
        # skips it - which is the correct outcome for a timeseries that has no
        # values here.
        #
        # 404 and 200-with-no-values are different events - the id is absent
        # versus the id exists and this window is empty - and the two messages
        # used to be worded so similarly ("No values for timeseries X ..." versus
        # "Timeseries X ... returned no values ...") that the difference read as
        # sloppiness rather than as information. They are now tallied under
        # distinct reasons, which states the distinction without spending a line.
        _tally.record(_NOT_FOUND, ts_id)
        return

    if _value_count(data) == 0:
        # 200 with an empty "values" list: the id exists, the window is empty.
        # Nothing to stage, and nothing to publish later either - see
        # _upload_one_ts_data for why an empty payload is not merely useless.
        _tally.record(_EMPTY_WINDOW, ts_id)
        return

    filesystem_store.write_json(data, office_id, TIMESERIES_FOLDER, ts_id, begin_str, end_str, "data")


def _upload_one_ts_data(ts_info):
    office_id = ts_info[0]
    ts_id = ts_info[1]
    begin = ts_info[2]
    end = ts_info[3]
    begin_str = begin.strftime(DATE_TIME_FORMAT)
    end_str = end.strftime(DATE_TIME_FORMAT)
    # DEBUG for the same reason as the extract side, and for one more: this line
    # announced an intent to publish items the extract phase had already reported
    # finding nothing for, and was then followed by a warning that there was
    # nothing to publish. 15 of 37 items in a single-project run said all three
    # things.
    logger.debug("Publishing timeseries %s for office %s [%s]", ts_id, office_id, log_util.window(begin, end))

    staged_data = filesystem_store.read_json(office_id, TIMESERIES_FOLDER, ts_id, begin_str, end_str, "data")
    if staged_data is None:
        raise FileNotFoundError(
            "No staged timeseries data found for this window."
        )

    if _value_count(staged_data) == 0:
        # Posting an empty payload achieves nothing, and cwms-python cannot even
        # do it: store_timeseries guards with "len(chunks) == 1" before computing
        # min(max_workers, len(chunks)), so zero values gives zero chunks and
        # ThreadPoolExecutor(max_workers=0) raises "max_workers must be greater
        # than 0". Its fetch path clamps with max(..., 1); the store path does
        # not. Staging skips empties now, but files written before that change -
        # or by an older build - are still on disk.
        _tally.record(_STAGED_EMPTY, ts_id)
        return

    cwms.store_timeseries(staged_data)


def _build_timeseries_work_items(
    office_id: str,
    timeseries_items: Iterable[TimeseriesConfig],
    default_start: str | None,
    default_end: str | None,
) -> list[list[object]]:
    valid = []

    for timeseries in timeseries_items:
        ts_id = _normalize_timeseries_id(office_id, timeseries.id)
        if ts_id is None:
            continue

        valid.append((ts_id, timeseries))

    unique, duplicates = log_util.dedupe(
        valid,
        key=lambda entry: (entry[0], entry[1].start_time or default_start, entry[1].end_time or default_end),
    )

    if duplicates:
        logger.warning(
            "%s in the config for office %s %s more than once; the duplicates are dropped, "
            "leaving %s. Duplicated: %s",
            log_util.plural(len(duplicates), "timeseries"),
            office_id,
            "appear" if len(duplicates) > 1 else "appears",
            log_util.plural(len(unique), "item"),
            ", ".join(sorted({ts_id for ts_id, _ in duplicates})),
        )

    return [
        [
            office_id,
            ts_id,
            _parse_timestamp(item.start_time or default_start, "start"),
            _parse_timestamp(item.end_time or default_end, "end"),
        ]
        for ts_id, item in unique
    ]


def _normalize_timeseries_id(office_id: str, configured_id: str) -> str | None:
    if configured_id.startswith(f"{office_id}."):
        configured_id = configured_id[len(office_id) + 1 :]

    if len(configured_id.split(".")) != 6:
        logger.warning(
            "Invalid time series id '%s'. Expected '[location].[parameter].[parameter_type].[interval].[duration].[version]' or office-prefixed equivalent.",
            configured_id,
        )
        return None

    return configured_id


def _parse_timestamp(value: str | None, label: str) -> datetime:
    if value is None:
        raise ValueError(f"Missing {label} time for timeseries processing.")

    normalized = value.strip()
    if normalized.lower() == "now":
        return datetime.now()

    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        try:
            return datetime.strptime(normalized, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"Invalid {label} time '{value}'. Use ISO-8601 or YYYY-MM-DD.") from exc


__all__ = ["publish_staged_timeseries", "stage_timeseries"]
