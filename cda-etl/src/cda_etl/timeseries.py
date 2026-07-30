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
from datetime import datetime
from typing import Iterable

import utils.cda_errors as cda_errors
import utils.threading_util as threading_util
import utils.filesystem_store as filesystem_store
import cwms
from config import TimeseriesConfig

logger = logging.getLogger(__name__)
DATE_TIME_FORMAT = "%Y-%m-%d %H.%M.%S"
TIMESERIES_FOLDER = "Timeseries"


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
    ts_info = _build_timeseries_work_items(office_id, timeseries, default_start, default_end)
    if not ts_info:
        logger.warning(f"No valid time series items found for office {office_id}")
        return

    logger.info("Staging %d timeseries data item(s) for office %s", len(ts_info), office_id)
    threading_util.execute_tasks(_download_one_ts_data, ts_info)
    logger.info("Completed staging timeseries data for office %s", office_id)


def publish_staged_timeseries(
    office_id: str,
    timeseries: Iterable[TimeseriesConfig],
    default_start: str | None,
    default_end: str | None,
) -> None:
    ts_info = _build_timeseries_work_items(office_id, timeseries, default_start, default_end)
    if not ts_info:
        logger.warning(f"No valid time series items found for office {office_id}")
        return

    logger.info("Publishing %d staged timeseries item(s) for office %s", len(ts_info), office_id)
    threading_util.execute_tasks(_upload_one_ts_data, ts_info)
    logger.info("Completed publishing timeseries data for office %s", office_id)


def _download_one_ts_data(ts_info):
    office_id = ts_info[0]
    ts_id = ts_info[1]
    begin = ts_info[2]
    end = ts_info[3]
    begin_str = begin.strftime(DATE_TIME_FORMAT)
    end_str = end.strftime(DATE_TIME_FORMAT)
    logger.info("Refreshing staged timeseries %s for office %s from %s to %s", ts_id, office_id, begin_str, end_str)

    try:
        data = cwms.get_timeseries(ts_id, office_id, begin=begin, end=end).json
    except Exception as error:
        if not cda_errors.is_no_data(error):
            raise

        # Nothing is staged, so a later publish of this window finds no file and
        # skips it - which is the correct outcome for a timeseries that has no
        # values here.
        logger.info(
            "No values for timeseries %s in office %s between %s and %s; nothing staged.",
            ts_id,
            office_id,
            begin_str,
            end_str,
        )
        return

    if _value_count(data) == 0:
        # 200 with an empty "values" list: the id exists, the window is empty.
        # Nothing to stage, and nothing to publish later either - see
        # _upload_one_ts_data for why an empty payload is not merely useless.
        logger.info(
            "Timeseries %s in office %s returned no values between %s and %s; nothing staged.",
            ts_id,
            office_id,
            begin_str,
            end_str,
        )
        return

    filesystem_store.write_json(data, office_id, TIMESERIES_FOLDER, ts_id, begin_str, end_str, "data")


def _upload_one_ts_data(ts_info):
    office_id = ts_info[0]
    ts_id = ts_info[1]
    begin = ts_info[2]
    end = ts_info[3]
    begin_str = begin.strftime(DATE_TIME_FORMAT)
    end_str = end.strftime(DATE_TIME_FORMAT)
    logger.info("Publishing timeseries %s for office %s from %s to %s", ts_id, office_id, begin_str, end_str)

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
        logger.info(
            "Staged timeseries %s in office %s for %s to %s holds no values; nothing to publish.",
            ts_id,
            office_id,
            begin_str,
            end_str,
        )
        return

    cwms.store_timeseries(staged_data)


def _build_timeseries_work_items(
    office_id: str,
    timeseries_items: Iterable[TimeseriesConfig],
    default_start: str | None,
    default_end: str | None,
) -> list[list[object]]:
    work_items: list[list[object]] = []

    for timeseries in timeseries_items:
        ts_id = _normalize_timeseries_id(office_id, timeseries.id)
        if ts_id is None:
            continue

        begin = _parse_timestamp(timeseries.start_time or default_start, "start")
        end = _parse_timestamp(timeseries.end_time or default_end, "end")
        work_items.append([office_id, ts_id, begin, end])

    return work_items


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
