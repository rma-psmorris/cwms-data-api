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
Stops cwms-python retrying a 404 six times.

cwms-python retries a failed timeseries chunk up to six times with backoff. That
exists for a good reason - its own comment notes CDA occasionally returns 500s
from connection-pool exhaustion that succeed on retry, and 500 is deliberately
left out of the session-level status_forcelist so it can be handled there
instead. But the loop catches bare `Exception`, so a 404 is retried too.

A 404 means "no values in this window". It will still be 404 on the sixth
attempt. Retrying wastes six round trips plus backoff per empty chunk, and with
a whole association category broadcast to every project there are a lot of empty
chunks.

The session-level retry (urllib3) already excludes 404 - its status_forcelist is
[403, 429, 502, 503, 504] - so only the chunk loop needs correcting.

This replaces that one private function. It is a patch against library
internals, so it verifies the shape it expects first and declines to patch (with
a warning) rather than breaking if cwms-python is upgraded and has moved on.
Worth removing once cwms-python distinguishes definitive from transient errors
itself.
"""
from __future__ import annotations

import logging
from typing import Any

import utils.cda_errors as cda_errors

logger = logging.getLogger(__name__)

_TARGET_MODULE = "cwms.timeseries.timeseries"
_TARGET_FUNCTION = "_call_with_retry"
_ATTEMPTS_ATTRIBUTE = "_CHUNK_ATTEMPTS"

_patched = False


def disable_retry_on_missing_data() -> bool:
    """
    Patches cwms-python so a 404 fails its chunk immediately instead of being
    retried. Returns True if the patch was applied.

    Idempotent, so calling it from more than one entry point is safe.
    """
    global _patched

    if _patched:
        return True

    try:
        import importlib

        module = importlib.import_module(_TARGET_MODULE)
    except ImportError:
        logger.warning(
            "Could not import %s to disable retries on 404; empty windows will be retried.",
            _TARGET_MODULE,
        )
        return False

    original = getattr(module, _TARGET_FUNCTION, None)
    default_attempts = getattr(module, _ATTEMPTS_ATTRIBUTE, None)

    if not callable(original) or not isinstance(default_attempts, int):
        logger.warning(
            "%s.%s is not the shape expected, so retries on 404 are left alone. "
            "cwms-python has probably changed; this shim can likely be removed.",
            _TARGET_MODULE,
            _TARGET_FUNCTION,
        )
        return False

    def _call_with_retry(fn: Any, *args: Any, attempts: int = default_attempts) -> Any:
        for attempt in range(attempts):
            try:
                return fn(*args)
            except Exception as error:
                # A definitive "nothing here" will not become something else on
                # the next try, so surface it now. The caller treats it as an
                # ordinary empty result.
                if cda_errors.is_no_data(error):
                    raise

                if attempt == attempts - 1:
                    raise

                logger.warning(f"chunk attempt {attempt + 1}/{attempts} failed: {error}")

    setattr(module, _TARGET_FUNCTION, _call_with_retry)
    _patched = True
    logger.debug(
        "Patched %s.%s so a 404 is not retried.", _TARGET_MODULE, _TARGET_FUNCTION
    )

    return True


def _reset_for_tests() -> None:
    global _patched
    _patched = False


__all__ = ["disable_retry_on_missing_data"]
