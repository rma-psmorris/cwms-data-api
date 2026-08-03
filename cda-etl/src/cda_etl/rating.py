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
import threading
import time
from datetime import datetime
from typing import Iterable, Any

import cwms
import utils.filesystem_store as filesystem_store
import utils.cda_errors as cda_errors
import utils.log_util as log_util
import utils.threading_util as threading_util
from config import RatingConfig

logger = logging.getLogger(__name__)
DATE_TIME_FORMAT = "%Y-%m-%d %H.%M.%S"
RATINGS_FOLDER = "Ratings"

_NO_CURVE = "with no rating curve"
_NO_CURVE_INFERRED = "inferred absent from an ambiguous 500"

_tally = log_util.Tally()


def _start_batch() -> log_util.Tally:
    global _tally
    _tally = log_util.Tally()

    return _tally


def _label(work_item) -> str:
    if work_item[4]:
        return f"{work_item[1]} [POR]"

    return f"{work_item[1]} [{log_util.window(work_item[2], work_item[3])}]"

_AMBIGUOUS_LOCK = threading.Lock()
_AMBIGUOUS_COUNT: list[int] = []


# Counts the ratings skipped in the current batch because the values endpoint
# answered 500-that-means-404 (see cda_errors.is_ambiguous_rating_failure).
# Every project genuinely lacking a rating curve is plausible; *all* of them
# failing this way is far more likely an unwell instance, and that distinction
# is worth surfacing since the 500 itself cannot make it.
def _reset_ambiguous_skips() -> None:
    with _AMBIGUOUS_LOCK:
        _AMBIGUOUS_COUNT.clear()


def _record_ambiguous_skip() -> None:
    with _AMBIGUOUS_LOCK:
        _AMBIGUOUS_COUNT.append(1)


def _ambiguous_skip_count() -> int:
    with _AMBIGUOUS_LOCK:
        return len(_AMBIGUOUS_COUNT)


def _warn_if_every_rating_was_ambiguous(office_id: str, attempted: int) -> None:
    """
    Every project genuinely lacking a rating curve is plausible. Every rating in
    a batch failing with the ambiguous 500 is far more likely a sick instance,
    and because that 500 is indistinguishable from "missing" the run would
    otherwise report success having staged nothing.
    """
    skipped = _ambiguous_skip_count()

    if attempted > 1 and skipped == attempted:
        logger.warning(
            "All %d rating(s) for office %s were skipped on the ambiguous 500 from the ratings "
            "values endpoint. That pattern is more consistent with the service being unwell than "
            "with every rating genuinely being absent - check CDA before trusting this run's "
            "rating output.",
            attempted,
            office_id,
        )


def _handle_missing_rating(error: Exception, rating_id: str, office_id: str, window: str) -> bool:
    """
    Returns True if this failure means "no rating curve here" and the item should
    be skipped. Re-raise otherwise.
    """
    if cda_errors.is_no_data(error):
        logger.debug("No rating curve for %s in office %s%s; nothing staged.", rating_id, office_id, window)
        _tally.record(_NO_CURVE, rating_id)
        return True

    if cda_errors.is_ambiguous_rating_failure(error):
        _record_ambiguous_skip()
        # WARNING, not INFO: unlike a true 404 this is inferred from a 500 that
        # CDA also uses for real processing failures.
        logger.warning(
            "No rating curve for %s in office %s%s; nothing staged.",
            rating_id,
            office_id,
            window,
        )
        _tally.record(_NO_CURVE_INFERRED, rating_id)
        return True

    return False


def stage_ratings(
    office_id: str,
    ratings: Iterable[RatingConfig],
    default_start: str | None,
    default_end: str | None,
) -> None:
    ratings = list(ratings)
    work_items = _build_rating_work_items(office_id, ratings, default_start, default_end)
    if not work_items:
        logger.debug("No ratings configured for office %s; nothing to extract.", office_id)
        return

    tally = _start_batch()
    _reset_ambiguous_skips()
    started = time.monotonic()
    threading_util.execute_tasks(_download_one_rating, work_items, label=_label, tally=tally)
    _warn_if_every_rating_was_ambiguous(office_id, len(work_items))
    log_util.outcome(
        logger,
        action="Staged",
        noun="rating",
        total=len(work_items),
        tally=tally,
        office_id=office_id,
        elapsed=time.monotonic() - started,
    )


def publish_staged_ratings(
    office_id: str,
    ratings: Iterable[RatingConfig],
    default_start: str | None,
    default_end: str | None,
) -> None:
    ratings = list(ratings)
    work_items = _build_rating_work_items(office_id, ratings, default_start, default_end)
    if not work_items:
        logger.debug("No ratings configured for office %s; nothing to load.", office_id)
        return

    tally = _start_batch()
    started = time.monotonic()
    threading_util.execute_tasks(_upload_one_rating, work_items, label=_label, tally=tally)
    log_util.outcome(
        logger,
        action="Published",
        noun="rating",
        total=len(work_items),
        tally=tally,
        office_id=office_id,
        elapsed=time.monotonic() - started,
    )


def _download_one_rating(work_item: list[object]) -> None:
    office_id = work_item[0]
    rating_id = work_item[1]
    begin = work_item[2]
    end = work_item[3]
    por = work_item[4]

    if por:
        logger.info("Extracting rating XML (POR) for %s in office %s", rating_id, office_id)
        try:
            with cda_errors.ratings_request():
                rating_xml = cwms.get_ratings_xml(rating_id, office_id)
        except Exception as error:
            if not _handle_missing_rating(error, rating_id, office_id, ""):
                raise

            return

        filesystem_store.write_json(_xml_to_json_payload(rating_xml), office_id, RATINGS_FOLDER, f"{rating_id}.por")
        return

    logger.info(
        "Extracting rating XML %s for office %s [%s]",
        rating_id,
        office_id,
        log_util.window(begin, end),
    )
    try:
        with cda_errors.ratings_request():
            rating_xml = cwms.get_ratings_xml(rating_id, office_id, begin=begin, end=end)
    except Exception as error:
        # A resolved rating id that this project has no curve for is ordinary,
        # not a fault. Staging nothing means publish skips it.
        if not _handle_missing_rating(
            error, rating_id, office_id, f" between {log_util.window(begin, end)}"
        ):
            raise

        return

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
            "No staged rating data found."
        )

    rating_xml = staged_data.get("xml") if isinstance(staged_data, dict) else None
    if not rating_xml:
        raise ValueError(f"Staged rating data for {office_id}.{rating_id} is missing XML payload.")

    # store_template must be True. With False, CDA strips the <rating-template>
    # element out of the XML before storing (RatingController.removeTemplate),
    # so the POST carries a rating-spec referencing a template that is not
    # present. A destination that has never seen that template cannot resolve
    # the reference and answers 500 "Database Error".
    #
    # The staged XML holds template, spec and values together, and for a tool
    # whose job is to mirror the source, the template is part of the rating's
    # definition rather than something to drop. True is also cwms-python's
    # default and CDA's ("Also store updates to the rating template. Default:
    # true"); this was the one place the pipeline opted out of it.
    cwms.store_rating(rating_xml, store_template=True)


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
        rating_id = rating.id
        por = rating.period_of_record
        begin = None if por else _parse_timestamp(rating.start_time or default_start, "start")
        end = None if por else _parse_timestamp(rating.end_time or default_end, "end")
        work_items.append([office_id, rating_id, begin, end, por])

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
