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
from typing import Iterable

import cwms
import utils.filesystem_store as filesystem_store
import utils.threading_util as threading_util
from config import ClobConfig

logger = logging.getLogger(__name__)
CLOBS_FOLDER = "Clobs"


def stage_clobs(office_id: str, clobs: Iterable[ClobConfig]) -> None:
    clobs = list(clobs)
    work_items = [[office_id, clob.id] for clob in clobs if clob.id]

    if not work_items:
        logger.warning("No valid clob ids found for staging")
        return

    logger.info("Staging %d clob item(s) for office %s", len(work_items), office_id)
    threading_util.execute_tasks(_download_one_clob, work_items)
    logger.info("Completed staging clobs for office %s", office_id)


def publish_staged_clobs(office_id: str, clobs: Iterable[ClobConfig]) -> None:
    clobs = list(clobs)
    work_items = [[office_id, clob.id] for clob in clobs if clob.id]

    if not work_items:
        logger.warning("No valid clob ids found for publishing")
        return

    logger.info("Publishing %d staged clob item(s) for office %s", len(work_items), office_id)
    threading_util.execute_tasks(_upload_one_clob, work_items)
    logger.info("Completed publishing clobs for office %s", office_id)


def _download_one_clob(work_item: list[str]) -> None:
    office_id, clob_id = work_item
    logger.info("Refreshing staged clob %s for office %s", clob_id, office_id)
    clob_data = cwms.get_clob(clob_id, office_id).json
    filesystem_store.write_json(clob_data, office_id, CLOBS_FOLDER, clob_id)


def _upload_one_clob(work_item: list[str]) -> None:
    office_id, clob_id = work_item
    logger.info("Publishing clob %s for office %s", clob_id, office_id)

    clob_data = filesystem_store.read_json(office_id, CLOBS_FOLDER, clob_id)
    if clob_data is None:
        raise FileNotFoundError(
            f"No staged clob data found for {office_id}.{clob_id}. "
            "Clob publish skipped for this item."
        )

    cwms.store_clobs(clob_data, fail_if_exists=False)


__all__ = ["publish_staged_clobs", "stage_clobs"]
