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

import pytest
import requests

import cwms
import cwms.timeseries.timeseries as cwms_ts
import utils.cwms_compat as cwms_compat


def _api_error(status_code: int, body: str = "") -> "cwms.api.ApiError":
    response = requests.Response()
    response.status_code = status_code
    response.reason = "Not Found" if status_code == 404 else "Internal Server Error"
    response.url = "https://cda.test/cwms-data/timeseries?name=X"
    response._content = body.encode()

    return cwms.api.ApiError(response)


@pytest.fixture
def patched(monkeypatch):
    """
    Applies the shim against a restored copy of the original function, so the
    real module is left as it was afterwards.
    """
    monkeypatch.setattr(cwms_ts, "_call_with_retry", cwms_ts._call_with_retry)
    cwms_compat._reset_for_tests()
    assert cwms_compat.disable_retry_on_missing_data() is True
    yield cwms_ts._call_with_retry
    cwms_compat._reset_for_tests()


def test_a_404_is_not_retried(patched):
    """
    cwms-python's own loop catches bare Exception and retries six times with
    backoff. A 404 will still be 404 on the sixth attempt.
    """
    calls = []

    def fn():
        calls.append(1)
        raise _api_error(404, '{"message":"Not found."}')

    with pytest.raises(cwms.api.ApiError):
        patched(fn)

    assert len(calls) == 1


def test_a_chunked_404_message_is_not_retried(patched):
    """The chunked path loses the exception type, leaving only text."""
    calls = []

    def fn():
        calls.append(1)
        raise RuntimeError(
            "CWMS API Error (https://cda.test/timeseries). "
            'May be the result of an empty query. {"message":"Not found."}'
        )

    with pytest.raises(RuntimeError):
        patched(fn)

    assert len(calls) == 1


def test_a_500_is_still_retried(patched, caplog):
    """
    The retry exists because CDA returns transient 500s from connection-pool
    exhaustion that succeed on a later attempt, so that must keep working.
    """
    caplog.set_level(logging.WARNING)
    calls = []

    def fn():
        calls.append(1)
        raise _api_error(500, '{"message":"Database Error"}')

    with pytest.raises(cwms.api.ApiError):
        patched(fn)

    assert len(calls) == cwms_ts._CHUNK_ATTEMPTS
    assert "chunk attempt 1/6 failed" in caplog.text


def test_a_transient_500_still_recovers(patched):
    calls = []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise _api_error(500, '{"message":"Database Error"}')
        return "recovered"

    assert patched(fn) == "recovered"
    assert len(calls) == 3


def test_a_success_is_returned_immediately(patched):
    assert patched(lambda: "value") == "value"


def test_patching_is_idempotent(patched):
    first = cwms_ts._call_with_retry

    assert cwms_compat.disable_retry_on_missing_data() is True
    assert cwms_ts._call_with_retry is first


def test_declines_to_patch_if_the_library_shape_changed(monkeypatch, caplog):
    """
    This reaches into a library private, so an upgrade should degrade to the old
    behaviour with a warning rather than crash the run.
    """
    caplog.set_level(logging.WARNING)
    monkeypatch.delattr(cwms_ts, "_CHUNK_ATTEMPTS")
    cwms_compat._reset_for_tests()

    assert cwms_compat.disable_retry_on_missing_data() is False
    assert "not the shape expected" in caplog.text

    cwms_compat._reset_for_tests()
