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
from typing import Iterable
from urllib.parse import quote

import cwms
import utils.filesystem_store as filesystem_store
import utils.threading_util as threading_util
from config import PropertyConfig

logger = logging.getLogger(__name__)
PROPERTIES_FOLDER = "Properties"

# Staging writes one file per category - <office>/Properties/<category>.json,
# holding a JSON list of property records - rather than one file per property.
# A REGI office has three association categories but hundreds of properties in
# them, and a file per property made the staged tree grow with the data while
# every read and write paid a separate filesystem round trip. The category
# listing endpoint returns the whole category in a single request, so one file
# is also one request.
#
# The REST API has no bulk property store, so the publish side still POSTs each
# record on its own - it just parses them out of the one staged file.
_STAGE_WRITE_LOCK = threading.Lock()


def stage_properties(office_id: str, properties: Iterable[PropertyConfig]) -> None:
    properties = list(properties)
    all_category_ids = sorted({item.category_id for item in properties if item.category_id and item.all_in_category})
    specific_ids_by_category = _group_specific_ids(properties, skip_categories=all_category_ids)

    category_work_items = [[office_id, category_id] for category_id in all_category_ids]
    specific_work_items = [
        [office_id, category_id, *property_ids] for category_id, property_ids in specific_ids_by_category
    ]

    if not specific_work_items and not category_work_items:
        logger.warning("No valid property items found for staging")
        return

    logger.info(
        "Staging %d category-wide property group(s) and %d partial category group(s) for office %s",
        len(category_work_items),
        len(specific_work_items),
        office_id,
    )

    if category_work_items:
        threading_util.execute_tasks(_download_all_properties_in_category, category_work_items)

    if specific_work_items:
        threading_util.execute_tasks(_download_properties_in_category, specific_work_items)

    logger.info("Completed staging properties for office %s", office_id)


def publish_staged_properties(office_id: str, properties: Iterable[PropertyConfig]) -> None:
    properties = list(properties)
    all_category_ids = sorted({item.category_id for item in properties if item.category_id and item.all_in_category})
    specific_ids_by_category = _group_specific_ids(properties, skip_categories=all_category_ids)

    # Two shapes of work item, one task per category either way: [office,
    # category] publishes every record in the staged category file, and
    # [office, category, id, ...] publishes only the named ones.
    work_items = [[office_id, category_id] for category_id in all_category_ids]
    work_items.extend(
        [office_id, category_id, *property_ids] for category_id, property_ids in specific_ids_by_category
    )

    if not work_items:
        logger.warning("No valid property items found for publishing")
        return

    logger.info(
        "Publishing %d staged property category(ies) for office %s",
        len(work_items),
        office_id,
    )
    threading_util.execute_tasks(_upload_properties_in_category, work_items)
    logger.info("Completed publishing properties for office %s", office_id)


def _group_specific_ids(
    properties: Iterable[PropertyConfig], skip_categories: Iterable[str]
) -> list[tuple[str, list[str]]]:
    """
    Collapses the individually named properties into one entry per category.

    A category that is also declared "all: true" is dropped: the category-wide
    read already covers it, and letting both through would have two tasks
    writing the same file.
    """
    skip = set(skip_categories)
    ids_by_category: dict[str, set[str]] = {}

    for item in properties:
        if not item.category_id or not item.id or item.all_in_category or item.category_id in skip:
            continue

        ids_by_category.setdefault(item.category_id, set()).add(item.id)

    return [(category_id, sorted(ids_by_category[category_id])) for category_id in sorted(ids_by_category)]


def _download_all_properties_in_category(work_item: list[str]) -> None:
    office_id, category_id = work_item

    logger.info("Refreshing all staged properties for category %s in office %s", category_id, office_id)
    # The list endpoint takes *-mask parameters (see CDA's
    # PropertyController.getAll: OFFICE_MASK / CATEGORY_ID_MASK / NAME_MASK).
    # "office" and "category-id" belong to the single-property GET; passing
    # those here leaves every mask null and the listing returns nothing at all
    # rather than failing, so an "all: true" category silently stages zero
    # properties.
    category_response = cwms.api.get(
        endpoint="properties",
        params={
            "office-mask": office_id,
            "category-id-mask": category_id,
        },
        api_version=1,
    )

    entries = []
    for property_data in _iter_property_entries(category_response):
        if not _extract_property_name(property_data):
            logger.warning(
                "Skipping property in category %s for office %s because no property name was found.",
                category_id,
                office_id,
            )
            continue

        entries.append(property_data)

    # The listing is the authoritative snapshot of the category, so it replaces
    # the file outright instead of merging - a property deleted upstream should
    # not survive on disk.
    _write_category(office_id, category_id, _sort_entries(entries))
    logger.info(
        "Staged %d property item(s) for category %s in office %s",
        len(entries),
        category_id,
        office_id,
    )


def _download_properties_in_category(work_item: list[str]) -> None:
    office_id, category_id = work_item[0], work_item[1]
    property_ids = list(work_item[2:])

    logger.info(
        "Refreshing %d staged property item(s) for category %s in office %s",
        len(property_ids),
        category_id,
        office_id,
    )

    entries = []
    for property_id in property_ids:
        entries.append(
            cwms.api.get(
                endpoint=f"properties/{_encode_path_segment(property_id)}",
                params={
                    "office": office_id,
                    "category-id": category_id,
                },
                api_version=1,
            )
        )

    # Merged, not replaced. Properties are declared at both office and project
    # level, so several stage calls can target one category file, and a plain
    # write would leave only the last caller's records.
    with _STAGE_WRITE_LOCK:
        merged = _merge_entries(_read_category(office_id, category_id), entries)
        _write_category(office_id, category_id, merged)


def _upload_properties_in_category(work_item: list[str]) -> None:
    office_id, category_id = work_item[0], work_item[1]
    requested_ids = list(work_item[2:])

    entries = _read_category(office_id, category_id)
    if entries is None:
        raise FileNotFoundError("No staged property data found.")

    entries_by_name = {name: entry for entry in entries if (name := _extract_property_name(entry))}

    if requested_ids:
        selected = [(name, entries_by_name[name]) for name in requested_ids if name in entries_by_name]
        for name in requested_ids:
            if name not in entries_by_name:
                logger.warning(
                    "No staged property data found for %s/%s in office %s.", category_id, name, office_id
                )
    else:
        selected = list(entries_by_name.items())

    if not selected:
        raise FileNotFoundError("No staged property data found.")

    logger.info(
        "Publishing %d property item(s) for category %s in office %s",
        len(selected),
        category_id,
        office_id,
    )

    # No bulk store endpoint, so one request per record. Every record is
    # attempted before the task reports, so one rejected property does not hide
    # the rest of the category.
    failures: list[str] = []
    for property_name, property_data in selected:
        try:
            _upload_one_property(property_name, property_data)
        except Exception as error:  # noqa: BLE001 - reported together below
            logger.error(
                "Failed publishing property %s/%s for office %s. %s",
                category_id,
                property_name,
                office_id,
                error,
            )
            failures.append(f"{property_name}: {error}")

    if failures:
        raise RuntimeError(f"{len(failures)} of {len(selected)} property item(s) failed. {'; '.join(failures)}")


def _upload_one_property(property_name: str, property_data: dict) -> None:
    logger.debug("Publishing property %s", property_name)

    try:
        cwms.api.post(endpoint="properties", data=property_data, api_version=1)
    except cwms.api.ApiError as error:
        # If it already exists, update instead of failing the whole ETL run.
        if error.response.status_code != 409:
            raise

        cwms.api.patch(
            endpoint=f"properties/{_encode_path_segment(property_name)}", data=property_data, api_version=1
        )


def _read_category(office_id: str, category_id: str) -> list[dict] | None:
    staged = filesystem_store.read_json(office_id, PROPERTIES_FOLDER, category_id)
    if staged is None:
        return None

    return list(_iter_property_entries(staged))


def _write_category(office_id: str, category_id: str, entries: list[dict]) -> None:
    # Earlier builds staged <category>/<property>.json. Nothing reads that tree
    # now, and the ETL leaves it alone - clearing it is a one-off cleanup of the
    # staged data, not something a run should do.
    filesystem_store.write_json(entries, office_id, PROPERTIES_FOLDER, category_id)


def _merge_entries(existing: list[dict] | None, incoming: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}

    for entry in [*(existing or []), *incoming]:
        name = _extract_property_name(entry)
        if not name:
            continue

        merged[name] = entry

    return _sort_entries(merged.values())


def _sort_entries(entries: Iterable[dict]) -> list[dict]:
    return sorted(entries, key=lambda entry: _extract_property_name(entry) or "")


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
