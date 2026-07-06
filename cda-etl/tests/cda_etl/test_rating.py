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
import rating
from config import RatingConfig


def test_stage_ratings_por(mocker):
    mock_execute = mocker.patch("utils.threading_util.execute_tasks")
    ratings = [RatingConfig(id="SWT.EUFA.Stage;Flow.Standard.Production", enabled=True, raw={"por": True})]

    rating.stage_ratings("SWT", ratings, "2026-01-01", "2026-01-02")

    assert mock_execute.call_count == 1
    assert mock_execute.call_args_list[0].args[0] == rating._download_one_rating
    work_item = mock_execute.call_args_list[0].args[1][0]
    assert work_item[4] is True


def test_download_one_rating_por(mocker):
    mock_write_json = mocker.patch("utils.filesystem_store.write_json")
    mock_get_ratings_xml = mocker.patch("cwms.get_ratings_xml", return_value="<ratings>xml</ratings>")

    rating._download_one_rating(["SWT", "SWT.EUFA.Stage;Flow.Standard.Production", None, None, True])

    mock_get_ratings_xml.assert_called_once_with("SWT.EUFA.Stage;Flow.Standard.Production", "SWT")
    mock_write_json.assert_called_once_with(
        {"xml": "<ratings>xml</ratings>"},
        "SWT",
        "Ratings",
        "SWT.EUFA.Stage;Flow.Standard.Production.por",
    )


def test_upload_one_rating_uses_xml_only(mocker):
    mock_store_rating = mocker.patch("cwms.store_rating")
    mocker.patch("utils.filesystem_store.read_json", return_value={"xml": "<ratings>xml</ratings>"})

    rating._upload_one_rating(["SWT", "SWT.EUFA.Stage;Flow.Standard.Production", None, None, True])

    mock_store_rating.assert_called_once_with("<ratings>xml</ratings>", store_template=False)
