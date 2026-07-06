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
from urllib.parse import quote

import cwms
import utils.filesystem_store as filesystem_store
import utils.threading_util as threading_util
from config import PropertyConfig

logger = logging.getLogger(__name__)
PROPERTIES_FOLDER = "Properties"


def stage_properties(office_id: str, properties: Iterable[PropertyConfig]) -> None:
    properties = list(properties)
    specific_work_items = [
        [office_id, item.category_id, item.id]
        for item in properties
        if item.category_id and item.id and not item.all_in_category
    ]
    category_work_items = sorted(
        [[office_id, item.category_id] for item in properties if item.category_id and item.all_in_category]
    )

    if not specific_work_items and not category_work_items:
        logger.warning("No valid property items found for staging")
        return

    logger.info(
        "Staging %d property item(s) and %d category-wide property group(s) for office %s",
        len(specific_work_items),
        len(category_work_items),
        office_id,
    )

    if specific_work_items:
        threading_util.execute_tasks(_download_one_property, specific_work_items)

    if category_work_items:
        threading_util.execute_tasks(_download_all_properties_in_category, category_work_items)

    logger.info("Completed staging properties for office %s", office_id)


def publish_staged_properties(office_id: str, properties: Iterable[PropertyConfig]) -> None:
    properties = list(properties)
    specific_work_items = [
        [office_id, item.category_id, item.id]
        for item in properties
        if item.category_id and item.id and not item.all_in_category
    ]
    category_ids = sorted({item.category_id for item in properties if item.category_id and item.all_in_category})

    category_work_items: list[list[str]] = []
    for category_id in category_ids:
        for property_name in filesystem_store.list_json_stems(office_id, PROPERTIES_FOLDER, category_id):
            category_work_items.append([office_id, category_id, property_name])

    work_items = [*specific_work_items, *category_work_items]
    work_items = sorted({(office, category, property_id) for office, category, property_id in work_items})

    if not work_items:
        logger.warning("No valid property items found for publishing")
        return

    logger.info(
        "Publishing %d staged property item(s) for office %s",
        len(work_items),
        office_id,
    )
    threading_util.execute_tasks(_upload_one_property, [list(item) for item in work_items])
    logger.info("Completed publishing properties for office %s", office_id)


def _download_one_property(work_item: list[str]) -> None:
    office_id, category_id, property_id = work_item

    logger.info("Refreshing staged property %s/%s for office %s", category_id, property_id, office_id)
    property_data = cwms.api.get(
        endpoint=f"properties/{_encode_path_segment(property_id)}",
        params={
            "office": office_id,
            "category-id": category_id,
        },
        api_version=1,
    )
    filesystem_store.write_json(property_data, office_id, PROPERTIES_FOLDER, category_id, property_id)


def _download_all_properties_in_category(work_item: list[str]) -> None:
    office_id, category_id = work_item

    logger.info("Refreshing all staged properties for category %s in office %s", category_id, office_id)
    category_response = cwms.api.get(
        endpoint="properties",
        params={
            "office": office_id,
            "category-id": category_id,
        },
        api_version=1,
    )

    count = 0
    for property_data in _iter_property_entries(category_response):
        property_name = _extract_property_name(property_data)
        if not property_name:
            logger.warning(
                "Skipping property in category %s for office %s because no property name was found.",
                category_id,
                office_id,
            )
            continue

        filesystem_store.write_json(property_data, office_id, PROPERTIES_FOLDER, category_id, property_name)
        count += 1

    logger.info(
        "Staged %d property item(s) for category %s in office %s",
        count,
        category_id,
        office_id,
    )


def _upload_one_property(work_item: list[str]) -> None:
    office_id, category_id, property_id = work_item

    logger.info("Publishing property %s/%s for office %s", category_id, property_id, office_id)
    property_data = filesystem_store.read_json(office_id, PROPERTIES_FOLDER, category_id, property_id)
    if property_data is None:
        raise FileNotFoundError(
            f"No staged property data found for {office_id}.{category_id}.{property_id}. "
            "Property publish skipped for this item."
        )

    try:
        cwms.api.post(endpoint="properties", data=property_data, api_version=1)
    except cwms.api.ApiError as error:
        # If it already exists, update instead of failing the whole ETL run.
        if error.response.status_code != 409:
            raise

        cwms.api.patch(endpoint=f"properties/{_encode_path_segment(property_id)}", data=property_data, api_version=1)


def _iter_property_entries(response: object) -> Iterable[dict]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]

    if isinstance(response, dict):
        for key in ("properties", "entries", "items", "value"):
            nested = response.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]

        if "name" in response or "property-name" in response:
            return [response]

    return []


def _extract_property_name(property_data: dict) -> str | None:
    name = property_data.get("name") or property_data.get("property-name")
    if isinstance(name, str) and name.strip():
        return name

    property_id = property_data.get("id")
    if isinstance(property_id, str) and property_id.strip():
        return property_id

    return None


def _encode_path_segment(value: str) -> str:
    return quote(value, safe="")


__all__ = ["publish_staged_properties", "stage_properties"]
