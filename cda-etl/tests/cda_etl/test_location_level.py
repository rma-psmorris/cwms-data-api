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

import location_level
from config import LocationLevelConfig


def test_stage_location_levels_por(mocker):
    mock_execute = mocker.patch("utils.threading_util.execute_tasks")
    levels = [LocationLevelConfig(id="SWT.EUFA-Dam.Elev.Inst.0.Top of Flood", enabled=True, raw={"por": True})]

    location_level.stage_location_levels("SWT", levels, "2026-01-01", "2026-01-02")

    assert mock_execute.call_count == 1
    assert mock_execute.call_args_list[0].args[0] == location_level._download_one_location_level
    work_item = mock_execute.call_args_list[0].args[1][0]
    assert work_item[4] is True


def test_stage_location_levels_windowed(mocker):
    mock_execute = mocker.patch("utils.threading_util.execute_tasks")
    levels = [LocationLevelConfig(id="SWT.EUFA-Dam.Elev.Inst.0.Top of Flood", enabled=True, raw={"por": False})]

    location_level.stage_location_levels("SWT", levels, "2026-01-01", "2026-01-02")

    assert mock_execute.call_count == 1
    work_item = mock_execute.call_args_list[0].args[1][0]
    assert work_item[4] is False
    assert work_item[2] is not None
    assert work_item[3] is not None


def test_download_one_location_level_por(mocker):
    mock_write_json = mocker.patch("utils.filesystem_store.write_json")
    mock_get_levels = mocker.patch("cwms.get_location_levels")

    mock_response = MagicMock()
    mock_response.json = {"levels": [{"id": "x"}]}
    mock_get_levels.return_value = mock_response

    location_level._download_one_location_level(["SWT", "SWT.EUFA-Dam.Elev.Inst.0.Top of Flood", None, None, True])

    mock_get_levels.assert_called_once_with(
        level_id_mask="SWT.EUFA-Dam.Elev.Inst.0.Top of Flood",
        office_id="SWT",
    )
    mock_write_json.assert_called_once_with(
        {"levels": [{"id": "x"}]},
        "SWT",
        "LocationLevels",
        "SWT.EUFA-Dam.Elev.Inst.0.Top of Flood.por",
    )


def test_upload_one_location_level(mocker):
    mock_store_level = mocker.patch("cwms.store_location_level")
    mocker.patch("utils.filesystem_store.read_json", return_value={"levels": [{"id": "a"}, {"id": "b"}]})

    location_level._upload_one_location_level(["SWT", "SWT.EUFA-Dam.Elev.Inst.0.Top of Flood", None, None, True])

    assert mock_store_level.call_count == 2
