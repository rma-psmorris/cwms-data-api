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
from typing import Iterable, Any

import cwms
import utils.filesystem_store as filesystem_store
import utils.threading_util as threading_util
from config import RatingConfig

logger = logging.getLogger(__name__)
DATE_TIME_FORMAT = "%Y-%m-%d %H.%M.%S"
RATINGS_FOLDER = "Ratings"


def stage_ratings(
    office_id: str,
    ratings: Iterable[RatingConfig],
    default_start: str | None,
    default_end: str | None,
) -> None:
    work_items = _build_rating_work_items(office_id, ratings, default_start, default_end)
    if not work_items:
        logger.warning("No valid rating items found for office %s", office_id)
        return

    logger.info("Staging %d rating item(s) for office %s", len(work_items), office_id)
    threading_util.execute_tasks(_download_one_rating, work_items)
    logger.info("Completed staging ratings for office %s", office_id)


def publish_staged_ratings(
    office_id: str,
    ratings: Iterable[RatingConfig],
    default_start: str | None,
    default_end: str | None,
) -> None:
    work_items = _build_rating_work_items(office_id, ratings, default_start, default_end)
    if not work_items:
        logger.warning("No valid rating items found for office %s", office_id)
        return

    logger.info("Publishing %d staged rating item(s) for office %s", len(work_items), office_id)
    threading_util.execute_tasks(_upload_one_rating, work_items)
    logger.info("Completed publishing ratings for office %s", office_id)


def _download_one_rating(work_item: list[object]) -> None:
    office_id = work_item[0]
    rating_id = work_item[1]
    begin = work_item[2]
    end = work_item[3]
    por = work_item[4]

    if por:
        logger.info("Refreshing staged rating XML (POR) for %s in office %s", rating_id, office_id)
        rating_xml = cwms.get_ratings_xml(rating_id, office_id)
        filesystem_store.write_json(_xml_to_json_payload(rating_xml), office_id, RATINGS_FOLDER, f"{rating_id}.por")
        return

    begin_str = begin.strftime(DATE_TIME_FORMAT)
    end_str = end.strftime(DATE_TIME_FORMAT)
    logger.info(
        "Refreshing staged rating XML %s for office %s from %s to %s",
        rating_id,
        office_id,
        begin_str,
        end_str,
    )
    rating_xml = cwms.get_ratings_xml(rating_id, office_id, begin=begin, end=end)
    filesystem_store.write_json(_xml_to_json_payload(rating_xml), office_id, RATINGS_FOLDER, rating_id)


def _upload_one_rating(work_item: list[object]) -> None:
    office_id = work_item[0]
    rating_id = work_item[1]
    por = work_item[4]

    staged_data = filesystem_store.read_json(
        office_id,
        RATINGS_FOLDER,
        f"{rating_id}.por" if por else rating_id,
    )
    if staged_data is None:
        raise FileNotFoundError(
            f"No staged rating data found for {office_id}.{rating_id}. "
            "Rating publish skipped for this item."
        )

    rating_xml = staged_data.get("xml") if isinstance(staged_data, dict) else None
    if not rating_xml:
        raise ValueError(f"Staged rating data for {office_id}.{rating_id} is missing XML payload.")

    cwms.store_rating(rating_xml, store_template=False)


def _xml_to_json_payload(xml_value: Any) -> dict[str, str]:
    if isinstance(xml_value, bytes):
        xml_value = xml_value.decode("utf-8")

    return {"xml": str(xml_value)}


def _build_rating_work_items(
    office_id: str,
    ratings: Iterable[RatingConfig],
    default_start: str | None,
    default_end: str | None,
) -> list[list[object]]:
    work_items: list[list[object]] = []

    for rating in ratings:
        if not rating.id:
            continue

        por = rating.period_of_record
        begin = None if por else _parse_timestamp(rating.start_time or default_start, "start")
        end = None if por else _parse_timestamp(rating.end_time or default_end, "end")
        work_items.append([office_id, rating.id, begin, end, por])

    return work_items


def _parse_timestamp(value: str | None, label: str) -> datetime:
    if value is None:
        raise ValueError(f"Missing {label} time for rating processing.")

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


__all__ = ["publish_staged_ratings", "stage_ratings"]
