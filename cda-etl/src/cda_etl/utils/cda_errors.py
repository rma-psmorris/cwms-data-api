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
"""
Recognising CDA's "nothing here" answer.

CDA returns 404 for "this record has no values in the window you asked for",
which is an ordinary outcome rather than a fault. It is especially ordinary now
that whole association property categories are applied to every project: many
resolved ids have nothing for a given project and window, and a lockage count
for a project with no lock is simply absent.

Detecting it takes two forms, because cwms-python loses the exception type on
its chunked timeseries path - fetch_timeseries_chunks catches the ApiError, logs
it, and re-raises a plain RuntimeError carrying only the text. So check the
status code where the type survives, and the message where it does not.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger(__name__)

# "May be the result of an empty query." comes from cwms.api.ApiError.hint(),
# which emits it for 404 and nothing else.
_NOT_FOUND_MARKERS = (
    "May be the result of an empty query.",
    '"message":"Not found."',
    '"message": "Not found."',
)


def status_code_of(error: BaseException) -> int | None:
    return getattr(getattr(error, "response", None), "status_code", None)


def is_no_data(error: BaseException) -> bool:
    """
    True when an exception represents CDA answering 404 - no values for that id
    in that window - rather than a genuine failure.
    """
    if status_code_of(error) == 404:
        return True

    message = str(error)

    return any(marker in message for marker in _NOT_FOUND_MARKERS)


# The ratings *values* endpoint answers 500 where it should answer 404.
#
# RatingSpecController.getOne returns SC_NOT_FOUND properly, but
# RatingController.getOne only reaches its NOT_FOUND branch when the rating set
# comes back empty. A genuinely absent rating throws RatingException from
# hec.data, which is caught and mapped to 500 with this message. RatingException
# covers both "does not exist" and real processing failures, so the controller
# cannot tell them apart - and neither can we.
#
# This marker appears nowhere else in CDA, so matching it is much narrower than
# treating any 500 as missing data. It is still a guess: the same branch catches
# IOException, which is more likely a genuine fault. Hence
# _AMBIGUOUS, the WARNING level at the call site, and the batch-wide check in
# rating.py that notices when *everything* failed this way - which looks far more
# like an unwell instance than every project lacking a rating curve.
_AMBIGUOUS_RATING_MARKER = "Failed to process request to retrieve RatingSet"


def is_ambiguous_rating_failure(error: BaseException) -> bool:
    """
    True for the 500 the ratings values endpoint returns when a rating does not
    exist. Cannot be distinguished from a real processing failure, so callers
    should report it more loudly than a true 404.
    """
    return (
        status_code_of(error) == 500
        and _AMBIGUOUS_RATING_MARKER in str(error)
    )


_local = threading.local()


@contextmanager
def ratings_request() -> Iterator[None]:
    previous = in_ratings_request()
    _local.in_ratings_request = True
    try:
        yield
    finally:
        _local.in_ratings_request = previous


def in_ratings_request() -> bool:
    """
    True while this thread is inside a call to the ratings endpoints, where a 500
    is more likely to mean "no such rating" than a fault.
    """
    return getattr(_local, "in_ratings_request", False)


__all__ = [
    "in_ratings_request",
    "is_ambiguous_rating_failure",
    "is_no_data",
    "ratings_request",
    "status_code_of",
]
