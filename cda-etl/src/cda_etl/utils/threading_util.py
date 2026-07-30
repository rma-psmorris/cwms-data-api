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
import traceback
from datetime import datetime
from concurrent.futures import as_completed, ThreadPoolExecutor

logger = logging.getLogger(__name__)

_EXECUTOR: ThreadPoolExecutor

def init_executor(max_workers):
    global _EXECUTOR
    _EXECUTOR = ThreadPoolExecutor(max_workers=max_workers)


def _format_part(part):
    # Work items carry datetimes; str() on those spells out microseconds, which
    # is noise in a log line that already names the window.
    if isinstance(part, datetime):
        return part.strftime("%Y-%m-%d %H:%M:%S")

    return str(part)


def _format_item(item):
    if isinstance(item, list):
        return ", ".join(_format_part(part) for part in item)
    return _format_part(item)


def _friendly_exception_message(item, exc):
    """
    Frames an item's failure for the log.

    The item identifiers are rendered here, so the exception text deliberately
    does not repeat them - the domain modules raise short messages stating only
    what the item does not already say. The batch-level advice about running the
    stage phase is logged once per batch rather than once per item.
    """
    item_str = _format_item(item)
    details = str(exc)

    if isinstance(exc, FileNotFoundError):
        return f"Skipped '{item_str}': {details}"

    if "CWMS API Error" in details:
        return f"CWMS request failed for '{item_str}'. {details}"

    return f"Task failed for '{item_str}'. {details}"


class TaskExecutionError(RuntimeError):
    """
    Raised after a batch finishes when any task failed for a reason other than
    missing staged data.
    """

    def __init__(self, failures: list[tuple[object, BaseException]]):
        self.failures = failures
        summary = "; ".join(
            f"{_format_item(item)}: {exc}" for item, exc in failures[:_MAX_REPORTED_FAILURES]
        )
        remainder = len(failures) - _MAX_REPORTED_FAILURES
        if remainder > 0:
            summary = f"{summary}; and {remainder} more"

        super().__init__(f"{len(failures)} task(s) failed. {summary}")


_MAX_REPORTED_FAILURES = 5


def execute_tasks(task_func, items):
    """
    Runs task_func over items on the shared executor.

    Every item is attempted, so one bad id does not hide the rest, but a batch
    with any hard failure raises TaskExecutionError once it finishes. Silently
    warning and carrying on made a run that staged nothing exit 0: the publish
    phase would then either fail with a confusing "no staged data" or quietly
    publish whatever a previous run had left on disk.

    A missing staged file (FileNotFoundError) stays a warning rather than a
    failure - that is the modelled "this item was not staged, skip it" case,
    and it already has its own message.
    """
    futures_to_items = {
        _EXECUTOR.submit(task_func, item): item
        for item in items
    }

    failures: list[tuple[object, BaseException]] = []
    skipped = 0

    for future in as_completed(futures_to_items):
        item = futures_to_items[future]
        exception = future.exception()

        if exception is None:
            logger.debug(f"No error on execution for {item}")
            continue

        message = _friendly_exception_message(item, exception)

        if isinstance(exception, FileNotFoundError):
            skipped += 1
            logger.warning(message)
            continue

        logger.error(message)
        failures.append((item, exception))

    if skipped:
        # Once per batch, not once per item: with a whole association category
        # applied to every project, dozens of items can legitimately have nothing
        # staged, and repeating the advice on each line buries it.
        logger.warning(
            "Skipped %d of %d item(s) with no staged data. If that is unexpected, run the "
            "stage phase for this window first, or check the config.",
            skipped,
            len(futures_to_items),
        )

    if failures:
        raise TaskExecutionError(failures)


__all__ = ["TaskExecutionError", "execute_tasks", "init_executor"]
