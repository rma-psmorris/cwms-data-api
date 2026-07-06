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

import cwms
import utils.filesystem_store as filesystem_store
import utils.threading_util as threading_util
from config import LocationLevelConfig

logger = logging.getLogger(__name__)
DATE_TIME_FORMAT = "%Y-%m-%d %H.%M.%S"
LEVELS_FOLDER = "LocationLevels"


def stage_location_levels(
    office_id: str,
    levels: Iterable[LocationLevelConfig],
    default_start: str | None,
    default_end: str | None,
) -> None:
    work_items = _build_location_level_work_items(office_id, levels, default_start, default_end)
    if not work_items:
        logger.warning("No valid location level items found for office %s", office_id)
        return

    logger.info("Staging %d location level item(s) for office %s", len(work_items), office_id)
    threading_util.execute_tasks(_download_one_location_level, work_items)
    logger.info("Completed staging location levels for office %s", office_id)


def publish_staged_location_levels(
    office_id: str,
    levels: Iterable[LocationLevelConfig],
    default_start: str | None,
    default_end: str | None,
) -> None:
    work_items = _build_location_level_work_items(office_id, levels, default_start, default_end)
    if not work_items:
        logger.warning("No valid location level items found for office %s", office_id)
        return

    logger.info("Publishing %d staged location level item(s) for office %s", len(work_items), office_id)
    threading_util.execute_tasks(_upload_one_location_level, work_items)
    logger.info("Completed publishing location levels for office %s", office_id)


def _download_one_location_level(work_item: list[object]) -> None:
    office_id = work_item[0]
    level_id = work_item[1]
    begin = work_item[2]
    end = work_item[3]
    por = work_item[4]

    if por:
        logger.info("Refreshing staged location levels (POR) for %s in office %s", level_id, office_id)
        level_data = cwms.get_location_levels(level_id_mask=level_id, office_id=office_id).json
        filesystem_store.write_json(level_data, office_id, LEVELS_FOLDER, f"{level_id}.por")
        return

    begin_str = begin.strftime(DATE_TIME_FORMAT)
    end_str = end.strftime(DATE_TIME_FORMAT)
    logger.info(
        "Refreshing staged location level %s for office %s from %s to %s",
        level_id,
        office_id,
        begin_str,
        end_str,
    )

    level_data = cwms.get_location_levels(
        level_id_mask=level_id,
        office_id=office_id,
        begin=begin,
        end=end,
    ).json
    filesystem_store.write_json(level_data, office_id, LEVELS_FOLDER, level_id)


def _upload_one_location_level(work_item: list[object]) -> None:
    office_id = work_item[0]
    level_id = work_item[1]
    por = work_item[4]

    staged_data = filesystem_store.read_json(
        office_id,
        LEVELS_FOLDER,
        f"{level_id}.por" if por else level_id,
    )
    if staged_data is None:
        raise FileNotFoundError(
            f"No staged location level data found for {office_id}.{level_id}. "
            "Location level publish skipped for this item."
        )

    levels = staged_data.get("levels", []) if isinstance(staged_data, dict) else []
    if not levels:
        logger.info("No location level records to publish for %s in office %s", level_id, office_id)
        return

    for level_record in levels:
        cwms.store_location_level(level_record)


def _build_location_level_work_items(
    office_id: str,
    levels: Iterable[LocationLevelConfig],
    default_start: str | None,
    default_end: str | None,
) -> list[list[object]]:
    work_items: list[list[object]] = []

    for level in levels:
        if not level.id:
            continue

        por = level.period_of_record
        begin = None if por else _parse_timestamp(level.start_time or default_start, "start")
        end = None if por else _parse_timestamp(level.end_time or default_end, "end")
        work_items.append([office_id, level.id, begin, end, por])

    return work_items


def _parse_timestamp(value: str | None, label: str) -> datetime:
    if value is None:
        raise ValueError(f"Missing {label} time for location level processing.")

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


__all__ = ["publish_staged_location_levels", "stage_location_levels"]
