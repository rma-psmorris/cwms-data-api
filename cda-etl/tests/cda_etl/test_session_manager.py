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
import pytest

from session_manager import SessionEndpoints


def test_session_endpoints_trim_and_normalize_optional_values(monkeypatch):
    monkeypatch.setenv("DEST_CDA_URL", " https://dest.example/cwms-data ")
    monkeypatch.setenv("SOURCE_CDA_URL", "   ")
    monkeypatch.setenv("SOURCE_CDA_API_KEY", "")
    monkeypatch.setenv("DEST_CDA_API_KEY", "  dest-key  ")

    endpoints = SessionEndpoints.from_env()

    assert endpoints.dest_cda_url == "https://dest.example/cwms-data"
    assert endpoints.source_cda_url is None
    assert endpoints.source_cda_api_key is None
    assert endpoints.dest_cda_api_key == "dest-key"
    assert endpoints.has_source is False


def test_session_endpoints_require_non_empty_dest_url(monkeypatch):
    monkeypatch.setenv("DEST_CDA_URL", "   ")

    with pytest.raises(ValueError, match="Missing required environment variable DEST_CDA_URL"):
        SessionEndpoints.from_env()
