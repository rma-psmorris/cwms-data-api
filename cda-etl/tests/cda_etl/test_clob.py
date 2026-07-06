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
from unittest.mock import MagicMock

import clob
from config import ClobConfig


def test_stage_clobs(mocker):
    mock_execute = mocker.patch("utils.threading_util.execute_tasks")
    clobs = [ClobConfig(id="SWT.EUFA.PROJECT.NOTES", enabled=True, raw={})]

    clob.stage_clobs("SWT", clobs)

    mock_execute.assert_called_once_with(
        clob._download_one_clob,
        [["SWT", "SWT.EUFA.PROJECT.NOTES"]],
    )


def test_publish_staged_clobs(mocker):
    mock_execute = mocker.patch("utils.threading_util.execute_tasks")
    clobs = [ClobConfig(id="SWT.EUFA.PROJECT.NOTES", enabled=True, raw={})]

    clob.publish_staged_clobs("SWT", clobs)

    mock_execute.assert_called_once_with(
        clob._upload_one_clob,
        [["SWT", "SWT.EUFA.PROJECT.NOTES"]],
    )


def test_download_one_clob(mocker):
    mock_write_json = mocker.patch("utils.filesystem_store.write_json")
    mock_get_clob = mocker.patch("cwms.get_clob")

    mock_response = MagicMock()
    mock_response.json = {"id": "SWT.EUFA.PROJECT.NOTES", "value": "text"}
    mock_get_clob.return_value = mock_response

    clob._download_one_clob(["SWT", "SWT.EUFA.PROJECT.NOTES"])

    mock_get_clob.assert_called_once_with("SWT.EUFA.PROJECT.NOTES", "SWT")
    mock_write_json.assert_called_once_with(
        {"id": "SWT.EUFA.PROJECT.NOTES", "value": "text"},
        "SWT",
        "Clobs",
        "SWT.EUFA.PROJECT.NOTES",
    )


def test_upload_one_clob(mocker):
    mock_store_clobs = mocker.patch("cwms.store_clobs")
    mocker.patch(
        "utils.filesystem_store.read_json",
        return_value={"id": "SWT.EUFA.PROJECT.NOTES", "value": "text"},
    )

    clob._upload_one_clob(["SWT", "SWT.EUFA.PROJECT.NOTES"])

    mock_store_clobs.assert_called_once_with(
        {"id": "SWT.EUFA.PROJECT.NOTES", "value": "text"},
        fail_if_exists=False,
    )
