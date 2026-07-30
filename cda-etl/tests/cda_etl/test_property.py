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
import property
from config import PropertyConfig


def test_stage_properties(mocker):
    mock_execute = mocker.patch("utils.threading_util.execute_tasks")
    properties = [PropertyConfig(category_id="REGI", id="EUFA.ETL.FLAG", enabled=True, raw={})]

    property.stage_properties("SWT", properties)

    mock_execute.assert_called_once_with(
        property._download_one_property,
        [["SWT", "REGI", "EUFA.ETL.FLAG"]],
    )


def test_stage_properties_all_in_category(mocker):
    mock_execute = mocker.patch("utils.threading_util.execute_tasks")
    properties = [PropertyConfig(category_id="REGI", id="*", enabled=True, raw={}, all_in_category=True)]

    property.stage_properties("SWT", properties)

    assert mock_execute.call_count == 1
    mock_execute.assert_called_once_with(
        property._download_all_properties_in_category,
        [["SWT", "REGI"]],
    )


def test_publish_staged_properties(mocker):
    mock_execute = mocker.patch("utils.threading_util.execute_tasks")
    properties = [PropertyConfig(category_id="REGI", id="EUFA.ETL.FLAG", enabled=True, raw={})]

    property.publish_staged_properties("SWT", properties)

    mock_execute.assert_called_once_with(
        property._upload_one_property,
        [["SWT", "REGI", "EUFA.ETL.FLAG"]],
    )


def test_publish_staged_properties_all_in_category(mocker):
    mock_execute = mocker.patch("utils.threading_util.execute_tasks")
    mock_list = mocker.patch(
        "utils.filesystem_store.list_json_stems",
        return_value=["EUFA.ETL.FLAG", "EUFA.ETL.ENABLED"],
    )
    properties = [PropertyConfig(category_id="REGI", id="*", enabled=True, raw={}, all_in_category=True)]

    property.publish_staged_properties("SWT", properties)

    mock_list.assert_called_once_with("SWT", "Properties", "REGI")
    mock_execute.assert_called_once_with(
        property._upload_one_property,
        [["SWT", "REGI", "EUFA.ETL.ENABLED"], ["SWT", "REGI", "EUFA.ETL.FLAG"]],
    )


def test_download_one_property(mocker):
    mock_get = mocker.patch("cwms.api.get", return_value={"office-id": "SWT", "category": "REGI", "name": "EUFA.ETL.FLAG"})
    mock_write = mocker.patch("utils.filesystem_store.write_json")

    property._download_one_property(["SWT", "REGI", "EUFA.ETL.FLAG"])

    # The single-property GET takes "office" / "category-id" (not the *-mask
    # parameters the listing endpoint uses).
    mock_get.assert_called_once_with(
        endpoint="properties/EUFA.ETL.FLAG",
        params={
            "office": "SWT",
            "category-id": "REGI",
        },
        api_version=1,
    )
    mock_write.assert_called_once_with(
        {"office-id": "SWT", "category": "REGI", "name": "EUFA.ETL.FLAG"},
        "SWT",
        "Properties",
        "REGI",
        "EUFA.ETL.FLAG",
    )


def test_download_one_property_encodes_name_with_spaces(mocker):
    mock_get = mocker.patch("cwms.api.get", return_value={"office-id": "SWT", "category": "REGI PROD", "name": "EUFA ETL FLAG"})
    mocker.patch("utils.filesystem_store.write_json")

    property._download_one_property(["SWT", "REGI PROD", "EUFA ETL FLAG"])

    mock_get.assert_called_once_with(
        endpoint="properties/EUFA%20ETL%20FLAG",
        params={
            "office": "SWT",
            "category-id": "REGI PROD",
        },
        api_version=1,
    )


def test_download_all_properties_in_category(mocker):
    mock_get = mocker.patch(
        "cwms.api.get",
        return_value=[
            {"name": "EUFA.ETL.FLAG", "office-id": "SWT", "category": "REGI", "value": "Y"},
            {"name": "EUFA.ETL.ENABLED", "office-id": "SWT", "category": "REGI", "value": "true"},
        ],
    )
    mock_write = mocker.patch("utils.filesystem_store.write_json")

    property._download_all_properties_in_category(["SWT", "REGI"])

    mock_get.assert_called_once_with(
        endpoint="properties",
        params={
            "office-mask": "SWT",
            "category-id-mask": "REGI",
        },
        api_version=1,
    )
    assert mock_write.call_count == 2


def test_upload_one_property_post(mocker):
    mock_post = mocker.patch("cwms.api.post")
    mock_patch = mocker.patch("cwms.api.patch")
    mocker.patch(
        "utils.filesystem_store.read_json",
        return_value={"office-id": "SWT", "category": "REGI", "name": "EUFA.ETL.FLAG", "value": "Y"},
    )

    property._upload_one_property(["SWT", "REGI", "EUFA.ETL.FLAG"])

    mock_post.assert_called_once_with(
        endpoint="properties",
        data={"office-id": "SWT", "category": "REGI", "name": "EUFA.ETL.FLAG", "value": "Y"},
        api_version=1,
    )
    mock_patch.assert_not_called()


def test_upload_one_property_patch_on_conflict(mocker):
    api_error_type = __import__("cwms").api.ApiError
    response = type("Response", (), {"status_code": 409})()

    mock_post = mocker.patch("cwms.api.post", side_effect=api_error_type(response))
    mock_patch = mocker.patch("cwms.api.patch")
    mocker.patch(
        "utils.filesystem_store.read_json",
        return_value={"office-id": "SWT", "category": "REGI", "name": "EUFA.ETL.FLAG", "value": "Y"},
    )

    property._upload_one_property(["SWT", "REGI", "EUFA.ETL.FLAG"])

    mock_post.assert_called_once()
    mock_patch.assert_called_once_with(
        endpoint="properties/EUFA.ETL.FLAG",
        data={"office-id": "SWT", "category": "REGI", "name": "EUFA.ETL.FLAG", "value": "Y"},
        api_version=1,
    )


def test_upload_one_property_patch_on_conflict_encodes_name_with_spaces(mocker):
    api_error_type = __import__("cwms").api.ApiError
    response = type("Response", (), {"status_code": 409})()

    mocker.patch("cwms.api.post", side_effect=api_error_type(response))
    mock_patch = mocker.patch("cwms.api.patch")
    mocker.patch(
        "utils.filesystem_store.read_json",
        return_value={"office-id": "SWT", "category": "REGI PROD", "name": "EUFA ETL FLAG", "value": "Y"},
    )

    property._upload_one_property(["SWT", "REGI PROD", "EUFA ETL FLAG"])

    mock_patch.assert_called_once_with(
        endpoint="properties/EUFA%20ETL%20FLAG",
        data={"office-id": "SWT", "category": "REGI PROD", "name": "EUFA ETL FLAG", "value": "Y"},
        api_version=1,
    )


def test_iter_property_entries_handles_list_response():
    response = [{"name": "A"}, {"name": "B"}]

    assert property._iter_property_entries(response) == [{"name": "A"}, {"name": "B"}]


def test_iter_property_entries_handles_object_wrapped_response():
    response = {"properties": [{"name": "A"}, {"name": "B"}]}

    assert property._iter_property_entries(response) == [{"name": "A"}, {"name": "B"}]


def test_download_all_in_category_uses_mask_parameters(mocker):
    """
    CDA's PropertyController.getAll filters on OFFICE_MASK / CATEGORY_ID_MASK /
    NAME_MASK. Sending the single-property GET's "office" / "category-id"
    instead leaves the masks null and the listing returns nothing at all, so an
    "all: true" category silently stages zero properties.
    """
    mock_get = mocker.patch("cwms.api.get", return_value=[])
    mocker.patch("utils.filesystem_store.write_json")

    property._download_all_properties_in_category(["SWT", "LOCATION TIME SERIES ASSOCIATION"])

    mock_get.assert_called_once_with(
        endpoint="properties",
        params={
            "office-mask": "SWT",
            "category-id-mask": "LOCATION TIME SERIES ASSOCIATION",
        },
        api_version=1,
    )
