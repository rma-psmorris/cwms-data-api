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
import sys
import logging
import os
import re
from contextlib import contextmanager
from typing import Iterator
import location
import location_level
import project
import property
import rating
import timeseries
import clob
import utils.cwms_compat
import utils.threading_util
import utils.filesystem_store
from config import DownloadConfig, OfficeConfig, ProjectConfig
from session_manager import SessionManager

logger = logging.getLogger(__name__)

_RESPONSE_STATUS_PATTERN = re.compile(r"response=<Response \[(\d{3})\]>")
_NOT_CONFIGURED = "<not configured>"


def _read_env(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default

    normalized = value.strip()
    if not normalized:
        return default

    return normalized


# Which half of the pipeline is running, for log messages that would otherwise
# be ambiguous about direction. cwms-python's chunk-retry warning comes from
# _call_with_retry, which is shared by its fetch *and* store paths, so the
# message alone cannot say whether a failing chunk was a read from the source or
# a write to the destination.
_STAGE = "staging (reading from the source)"
_PUBLISH = "publishing (writing to the destination)"
_current_phase: str | None = None


@contextmanager
def _phase(name: str) -> Iterator[None]:
    global _current_phase
    previous = _current_phase
    _current_phase = name
    try:
        yield
    finally:
        _current_phase = previous


class _FriendlyCdaLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()

        if "CDA Error: response=" in message:
            # cwms.api logs this at ERROR on the root logger for any non-ok
            # response, before raising. The wording below is ours; the severity
            # was not, which meant one not-found produced an INFO line from the
            # caller and an ERROR line from here for the same event.
            match = _RESPONSE_STATUS_PATTERN.search(message)
            status_code = match.group(1) if match else "unknown"

            if status_code == "404":
                # Not-found is a modelled outcome throughout cda-etl: a
                # timeseries with no values in the window, a rating or location
                # level a project does not have. The caller decides, and either
                # skips it or lets it abort the run.
                record.levelno = logging.INFO
                record.levelname = "INFO"
                record.msg = (
                    "CWMS API request returned HTTP 404 (nothing found). Whether that matters "
                    "is decided by the caller; see the next log line for the endpoint."
                )
                record.args = ()
                return True

            record.msg = (
                "CWMS API request returned HTTP %s. "
                "See the next log line for endpoint and server details."
            )
            record.args = (status_code,)
            return True

        # cwms-python's fetch_timeseries_chunks logs at ERROR before raising, so
        # a 404 - "no values in this window" - arrives as "Failed to fetch data",
        # which reads like a fault. timeseries._download_one_ts_data treats that
        # case as normal and stages nothing, so soften the log to match. The
        # hint string is only produced for 404.
        if message.startswith("Failed to fetch data") and (
            "May be the result of an empty query." in message
            or '"message":"Not found."' in message
        ):
            record.levelno = logging.INFO
            record.levelname = "INFO"
            record.msg = (
                "No values in this window (CDA answered 404); nothing staged for it. Details: %s"
            )
            record.args = (message,)
            return True

        if message.startswith("chunk attempt") and "CWMS API Error" in message:
            # Do not guess the direction. This warning previously said "upload"
            # unconditionally, which sent people looking at DEST_CDA_URL for
            # what was actually a failed read from the source.
            phase = _current_phase or "an unknown phase"
            record.msg = (
                "Timeseries chunk failed during %s and will be retried. "
                "The URL in the details below is the one that failed. Details: %s"
            )
            record.args = (phase, message)
            return True

        return True


def pipeline(config: DownloadConfig, session_manager: SessionManager) -> None:
    if session_manager.has_source_session:
        with session_manager.source_session(), _phase(_STAGE):
            for office in config.offices(enabled_only=True):
                logger.info(f"Processing office {office.id}")

                _stage_office_data(office)

                for project_config in office.projects(enabled_only=True):
                    _stage_project_data(project_config, config)
    else:
        logger.info("SOURCE_CDA_URL is not configured; skipping source download and using staged files only.")

    with session_manager.dest_session(), _phase(_PUBLISH):
        for office in config.offices(enabled_only=True):
            logger.info(f"Publishing office {office.id}")

            _publish_office_data(office)

            for project_config in office.projects(enabled_only=True):
                _publish_project_data(project_config, config)


def _stage_office_data(office_config: OfficeConfig) -> None:
    office_properties = list(office_config.properties(enabled_only=True))

    logger.info(
        "Stage inputs for office %s: %d global propert(y/ies)",
        office_config.id,
        len(office_properties),
    )

    logger.info("Staging global properties for office %s", office_config.id)
    property.stage_properties(office_config.id, office_properties)


def _publish_office_data(office_config: OfficeConfig) -> None:
    office_properties = list(office_config.properties(enabled_only=True))

    logger.info(
        "Publish inputs for office %s: %d global propert(y/ies)",
        office_config.id,
        len(office_properties),
    )

    logger.info("Publishing global properties for office %s", office_config.id)
    property.publish_staged_properties(office_config.id, office_properties)


def _stage_project_data(project_config: ProjectConfig, config: DownloadConfig) -> None:
    logger.info(f"Staging project {project_config.qualified_id}")

    project_locations = list(project_config.locations(enabled_only=True))
    project_timeseries = list(project_config.timeseries(enabled_only=True))
    project_clobs = list(project_config.clobs(enabled_only=True))
    project_levels = list(project_config.location_levels(enabled_only=True))
    project_ratings = list(project_config.ratings(enabled_only=True))
    project_properties = list(project_config.properties(enabled_only=True))

    logger.info(
        "Stage inputs for %s: %d location(s), %d timeseries item(s), %d clob(s), %d location level(s), %d rating(s), %d propert(y/ies)",
        project_config.qualified_id,
        len(project_locations),
        len(project_timeseries),
        len(project_clobs),
        len(project_levels),
        len(project_ratings),
        len(project_properties),
    )

    logger.info("Staging locations for project %s", project_config.qualified_id)
    location.stage_locations(project_config.office_id, project_locations)
    logger.info("Staging project record for %s", project_config.qualified_id)
    project.stage_projects([project_config])
    logger.info("Staging timeseries data for project %s", project_config.qualified_id)
    timeseries.stage_timeseries(
        project_config.office_id,
        project_timeseries,
        config.settings.start_time,
        config.settings.end_time,
    )
    logger.info("Staging clobs for project %s", project_config.qualified_id)
    clob.stage_clobs(project_config.office_id, project_clobs)
    logger.info("Staging location levels for project %s", project_config.qualified_id)
    location_level.stage_location_levels(
        project_config.office_id,
        project_levels,
        config.settings.start_time,
        config.settings.end_time,
    )
    logger.info("Staging ratings for project %s", project_config.qualified_id)
    rating.stage_ratings(
        project_config.office_id,
        project_ratings,
        config.settings.start_time,
        config.settings.end_time,
    )
    logger.info("Staging properties for project %s", project_config.qualified_id)
    property.stage_properties(project_config.office_id, project_properties)
    logger.info("Completed staging for project %s", project_config.qualified_id)


def _log_startup_configuration(config: DownloadConfig, session_manager: SessionManager) -> None:
    source_url = session_manager.endpoints.source_cda_url or _NOT_CONFIGURED
    dest_url = session_manager.endpoints.dest_cda_url
    start_time = config.settings.start_time or _NOT_CONFIGURED
    end_time = config.settings.end_time or _NOT_CONFIGURED

    logger.info("Startup configuration")
    logger.info("  Data source      : %s", source_url)
    logger.info("  Data destination : %s", dest_url)
    logger.info("  Time window      : start=%s end=%s", start_time, end_time)


def _publish_project_data(project_config: ProjectConfig, config: DownloadConfig) -> None:
    logger.info(f"Publishing project {project_config.qualified_id}")

    project_locations = list(project_config.locations(enabled_only=True))
    project_timeseries = list(project_config.timeseries(enabled_only=True))
    project_clobs = list(project_config.clobs(enabled_only=True))
    project_levels = list(project_config.location_levels(enabled_only=True))
    project_ratings = list(project_config.ratings(enabled_only=True))
    project_properties = list(project_config.properties(enabled_only=True))

    logger.info(
        "Publish inputs for %s: %d location(s), %d timeseries item(s), %d clob(s), %d location level(s), %d rating(s), %d propert(y/ies)",
        project_config.qualified_id,
        len(project_locations),
        len(project_timeseries),
        len(project_clobs),
        len(project_levels),
        len(project_ratings),
        len(project_properties),
    )

    logger.info("Publishing locations for project %s", project_config.qualified_id)
    location.publish_staged_locations(project_config.office_id, project_locations)
    logger.info("Publishing project record for %s", project_config.qualified_id)
    project.publish_staged_projects([project_config])
    logger.info("Publishing timeseries data for project %s", project_config.qualified_id)
    timeseries.publish_staged_timeseries(
        project_config.office_id,
        project_timeseries,
        config.settings.start_time,
        config.settings.end_time,
    )
    logger.info("Publishing clobs for project %s", project_config.qualified_id)
    clob.publish_staged_clobs(project_config.office_id, project_clobs)
    logger.info("Publishing location levels for project %s", project_config.qualified_id)
    location_level.publish_staged_location_levels(
        project_config.office_id,
        project_levels,
        config.settings.start_time,
        config.settings.end_time,
    )
    logger.info("Publishing ratings for project %s", project_config.qualified_id)
    rating.publish_staged_ratings(
        project_config.office_id,
        project_ratings,
        config.settings.start_time,
        config.settings.end_time,
    )
    logger.info("Publishing properties for project %s", project_config.qualified_id)
    property.publish_staged_properties(project_config.office_id, project_properties)
    logger.info("Completed publish for project %s", project_config.qualified_id)


def _initialize_runtime():
    # Defaults to the expander's output. Ids that an application derives from
    # CWMS association properties (or, later, PublishedTimeSeries/A2W) are
    # resolved into literal ids by cda-expander before this runs; cda-etl only
    # ever reads literal ids. See docs/id-resolution-work-plan.md.
    # A 404 means "no values in that window" and will still be 404 on the sixth
    # attempt, so stop cwms-python retrying it. See utils/cwms_compat.py.
    utils.cwms_compat.disable_retry_on_missing_data()

    config_path = _read_env("REGI_CONFIG_PATH", "regi.generated.yml")
    config = DownloadConfig.from_yaml(config_path)
    session_manager = SessionManager.from_env()
    utils.threading_util.init_executor(config.settings.max_threads)

    # settings.path in a committed config is written for the container, where
    # compose mounts ./cda-etl/data/regi at /data/regi. Running outside the
    # container that absolute path resolves against the current drive instead
    # (C:\data\regi on Windows), so allow an override rather than making a local
    # run require editing - and then reverting - committed config.
    storage_root = _read_env("REGI_DATA_PATH", config.settings.path)
    utils.filesystem_store.set_storage_root(storage_root)

    config_log_level = getattr(logging, config.settings.log_level.upper(), logging.INFO)
    logging.getLogger().setLevel(config_log_level)
    _log_startup_configuration(config, session_manager)

    return config, session_manager


__all__ = ["pipeline"]


if __name__ == "__main__":
    log_level_str = _read_env("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    logging.basicConfig(level=log_level)
    for handler in logging.getLogger().handlers:
        handler.addFilter(_FriendlyCdaLogFilter())

    logger.debug(f"Using log level: {log_level_str}")

    try:
        config, session_manager = _initialize_runtime()

        try:
            pipeline(config, session_manager)
        except Exception:
            logger.exception("Unhandled exception occurred during ETL pipeline execution")
            sys.exit(1)

    except Exception:
        logger.exception("Unhandled exception occurred during initialization")
        sys.exit(1)

