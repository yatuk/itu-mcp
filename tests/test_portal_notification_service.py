"""GetNotification is asked with the ``all`` parameter the Portal page sends (synthetic data)."""

from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from ninova_mcp.client import NinovaError
from ninova_mcp.parsing_portal import extract_notifications, notification_list_is_script_filled
from ninova_mcp.portal_data import (
    NOTIFICATION_CONTENT_LIMIT,
    notification_params,
    notifications_from_api,
    portal_call,
)
from ninova_mcp.server import NinovaMcpApp
from ninova_mcp.tool_metadata.portal import PORTAL_TOOLS

from recording import offline_app

PAGE = "https://portal.itu.edu.tr/apps/default/"
SERVICE = PAGE + "service/service.aspx/GetNotification"
JSON_TYPE = {"Content-Type": "application/json; charset=utf-8"}
ERROR_PAGE = (200, {"Content-Type": "text/html; charset=utf-8"},
              b"<!DOCTYPE html><html><body>internal server error</body></html>")


def row(object_id: int, is_read: int = 1, **extra: object) -> dict:
    return {
        "ObjectId": object_id, "Title": f"Sentetik bildirim {object_id}", "SummaryText": f"Ozet {object_id}",
        "ContentText": f"<p>Govde <b>{object_id}</b></p><script>x()</script>", "CreateDate": "04.03.2041 10:05:00",
        "BeforeCreateDate": "2 g ", "IsRead": is_read, **extra,
    }


RECENT = [row(901, 0), row(902, 1), row(903, 0), row(904, 1)]
EVERYTHING = RECENT + [row(number) for number in range(905, 917)]
# What the served home page carries: the list element with nothing in it.
EMPTY_LIST_PAGE = '<html><body><ul class="list-unstyled notification__list"></ul></body></html>'
RENDERED_LIST_PAGE = (
    '<ul class="list-unstyled notification__list">'
    '<li class="notification__list-item notification__list-item--unread"><a href="#">'
    '<span class="pull-left">Sentetik baslik</span><span class="pull-right">3 s</span></a></li>'
    '<li class="notification__list-item"><a href="#"><span class="pull-left">Okunmus</span></a></li></ul>'
)


def reply(rows: list[dict]) -> tuple:
    return (200, JSON_TYPE, json.dumps({"d": {"StatusCode": 0, "NotificationInformationList": rows}}).encode())


def service_script() -> dict:
    # Without the parameter the service answers with an error page and HTTP 200.
    return {SERVICE: ERROR_PAGE, SERVICE + "?all=false": reply(RECENT), SERVICE + "?all=true": reply(EVERYTHING)}


class RequestParameterTests(unittest.TestCase):
    def service_urls(self, adapter: object) -> list[str]:
        return [url for url in adapter.urls if "GetNotification" in url]  # type: ignore[attr-defined]

    def test_default_call_sends_all_false_and_reads_the_recent_ones(self) -> None:
        app, adapter = offline_app(self, service_script())
        result = app.obs_get_notifications()
        self.assertEqual(self.service_urls(adapter), [SERVICE + "?all=false"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual((result["count"], result["total_count"], result["unread_count"]), (4, 4, 2))
        self.assertEqual(result["scope"], "recent")
        self.assertIn("include_all", result["scope_note"])
        self.assertNotIn("api_fallback", result)
        self.assertEqual(set(adapter.hosts), {"portal.itu.edu.tr"})

    def test_include_all_sends_all_true(self) -> None:
        app, adapter = offline_app(self, service_script())
        result = app.obs_get_notifications(include_all=True)
        self.assertEqual(self.service_urls(adapter), [SERVICE + "?all=true"])
        self.assertEqual((result["count"], result["total_count"], result["scope"]), (16, 16, "all"))
        self.assertNotIn("scope_note", result)
        self.assertEqual(app.obs_get_notifications(include_all=True, limit=5)["count"], 5)

    def test_an_explicit_limit_above_the_recent_list_reads_the_whole_list(self) -> None:
        app, adapter = offline_app(self, service_script())
        result = app.obs_get_notifications(limit=10)
        self.assertEqual(self.service_urls(adapter), [SERVICE + "?all=false", SERVICE + "?all=true"])
        self.assertEqual((result["count"], result["total_count"], result["scope"]), (10, 16, "all"))

    def test_an_explicit_limit_the_recent_list_covers_needs_one_request(self) -> None:
        app, adapter = offline_app(self, service_script())
        result = app.obs_get_notifications(limit=3)
        self.assertEqual(self.service_urls(adapter), [SERVICE + "?all=false"])
        self.assertEqual((result["count"], result["total_count"], result["unread_count"]), (3, 4, 2))

    def test_recent_rows_are_kept_when_the_whole_list_cannot_be_read(self) -> None:
        script = service_script()
        script[SERVICE + "?all=true"] = ERROR_PAGE
        app, _ = offline_app(self, script)
        result = app.obs_get_notifications(limit=50)
        self.assertEqual((result["status"], result["count"], result["scope"]), ("ok", 4, "recent"))
        self.assertIn("could not be read", result["scope_note"])

    def test_parameter_values(self) -> None:
        self.assertEqual(notification_params(False), {"all": "false"})
        self.assertEqual(notification_params(True), {"all": "true"})

    def test_portal_call_passes_parameters_only_when_given(self) -> None:
        get_json = Mock(return_value={})
        portal_call(get_json, "GetNotification", {"all": "false"})
        get_json.assert_called_once_with("GetNotification", params={"all": "false"})
        get_json.reset_mock()
        portal_call(get_json, "GetBalance")
        get_json.assert_called_once_with("GetBalance")

    def test_the_other_parameterless_operations_are_called_as_before(self) -> None:
        app, adapter = offline_app(self)
        for call in (app.obs_get_campus_card, app.obs_get_cloud_quota, app.obs_get_help_tickets):
            try:
                call()
            except NinovaError:
                pass
        service = [url.rsplit("/", 1)[-1] for url in adapter.urls if "/service.aspx/" in url]
        self.assertEqual(service, ["GetBalance", "GetQuota", "GetQuotaEski", "GetYardim"])


class ListItemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app, _ = offline_app(self, service_script())

    def test_fields_of_a_listed_notification(self) -> None:
        first, second = self.app.obs_get_notifications()["notifications"][:2]
        self.assertEqual(first, {
            "id": "901", "title": "Sentetik bildirim 901", "summary": "Ozet 901",
            "created_at": "2041-03-04T10:05:00", "age": "2 g", "is_read": False, "unread": True,
        })
        self.assertEqual((second["is_read"], second["unread"]), (True, False))

    def test_the_body_is_returned_for_the_requested_notification_only(self) -> None:
        for item in self.app.obs_get_notifications(include_all=True)["notifications"]:
            self.assertNotIn("content", item)
        result = self.app.obs_get_notifications(notification_id="910")
        self.assertEqual(result["notification"]["id"], "910")
        self.assertEqual(result["notification"]["content"], "Govde 910")
        self.assertFalse(result["notification"]["content_truncated"])
        self.assertTrue(result["untrusted_external_content"])

    def test_an_id_given_as_a_number_is_found_and_an_unknown_one_is_refused(self) -> None:
        self.assertEqual(self.app.obs_get_notifications(notification_id=903)["notification"]["id"], "903")  # type: ignore[arg-type]
        with self.assertRaisesRegex(NinovaError, "bulunamadı"):
            self.app.obs_get_notifications(notification_id="999")


class NormalizationTests(unittest.TestCase):
    def one(self, **fields: object) -> dict:
        return notifications_from_api({"StatusCode": 0, "NotificationInformationList": [row(1, **fields)]})[0]

    def test_create_date_formats(self) -> None:
        cases = {
            "04.03.2041 10:05:00": "2041-03-04T10:05:00",  # dotted: day first
            "04.03.2041 10:05": "2041-03-04T10:05:00",
            "04.03.2041": "2041-03-04",
            "3/4/2041 10:05:00": "2041-03-04T10:05:00",  # slashed: month first
            "03/04/2041": "2041-03-04",
            "2041-03-04T10:05:00": "2041-03-04T10:05:00",
            "/Date(2246004300000)/": "2041-03-04T10:05:00+00:00",
            "\\/Date(2246004300000)\\/": "2041-03-04T10:05:00+00:00",
            "dün": "dün",
            "": None,
        }
        for given, expected in cases.items():
            with self.subTest(given=given):
                self.assertEqual(self.one(CreateDate=given)["created_at"], expected)
        self.assertIsNone(self.one(CreateDate=None)["created_at"])

    def test_read_state(self) -> None:
        for given, is_read, unread in ((0, False, True), ("0", False, True), (False, False, True),
                                       (1, True, False), ("1", True, False), (True, True, False),
                                       (None, None, False)):
            with self.subTest(given=given):
                item = self.one(IsRead=given)
                self.assertEqual((item["is_read"], item["unread"]), (is_read, unread))

    def test_long_html_content_is_cleaned_and_bounded(self) -> None:
        body = "<div><p>" + "kelime " * 5000 + "</p><style>p{}</style></div>"
        item = self.one(ContentText=body, SummaryText="<b>Kisa</b>  ozet " + "x" * 2000)
        self.assertTrue(item["content_truncated"])
        self.assertLessEqual(len(item["content"]), NOTIFICATION_CONTENT_LIMIT + 10)
        self.assertNotIn("<", item["content"])
        self.assertTrue(item["summary"].startswith("Kisa ozet"))
        self.assertLessEqual(len(item["summary"]), 510)

    def test_missing_summary_and_age(self) -> None:
        item = self.one(SummaryText=None, BeforeCreateDate=None)
        self.assertIsNone(item["summary"])
        self.assertIsNone(item["age"])


class HomepageFallbackTests(unittest.TestCase):
    def call(self, html: str) -> dict:
        app = NinovaMcpApp()
        app._client = Mock()
        with patch.object(app, "_get_portal_json", side_effect=NinovaError("HTML page instead of JSON")), \
                patch.object(app, "_get_portal_page", return_value=(html, PAGE)):
            return app.obs_get_notifications()

    def test_empty_script_filled_list_is_reported_as_such(self) -> None:
        self.assertTrue(notification_list_is_script_filled(EMPTY_LIST_PAGE))
        result = self.call(EMPTY_LIST_PAGE)
        self.assertEqual((result["status"], result["count"], result["notifications"]), ("unavailable", None, None))
        self.assertIn("fills by script", result["reason"])
        self.assertIn("not an empty inbox", result["reason"])
        self.assertTrue(result["api_fallback"])

    def test_page_without_the_list_keeps_the_old_reason(self) -> None:
        self.assertFalse(notification_list_is_script_filled("<html></html>"))
        result = self.call("<html></html>")
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("carries no notification list", result["reason"])

    def test_server_rendered_list_is_still_read(self) -> None:
        self.assertFalse(notification_list_is_script_filled(RENDERED_LIST_PAGE))
        self.assertEqual(extract_notifications(RENDERED_LIST_PAGE, PAGE)["count"], 2)
        result = self.call(RENDERED_LIST_PAGE)
        self.assertEqual((result["status"], result["count"]), ("ok", 2))
        self.assertEqual(result["notifications"][0],
                         {"title": "Sentetik baslik", "time_ago": "3 s", "unread": True, "notification_id": None})
        self.assertEqual(result["source_kind"], "homepage_html_fallback")


class MetadataTests(unittest.TestCase):
    def test_include_all_is_typed_and_described(self) -> None:
        tool = next(tool for tool in PORTAL_TOOLS if tool["name"] == "obs_get_notifications")
        properties = tool["inputSchema"]["properties"]
        self.assertEqual(properties["include_all"]["type"], "boolean")
        self.assertIs(properties["include_all"]["default"], False)
        self.assertTrue(properties["include_all"]["description"])
        self.assertNotIn("default", properties["limit"])
        for word in ("unread_count", "is_read", "include_all", "notification_id"):
            self.assertIn(word, tool["description"])


if __name__ == "__main__":
    unittest.main()
