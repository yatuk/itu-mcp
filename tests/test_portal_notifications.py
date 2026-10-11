"""A failed notification read must not look like an empty inbox."""

from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

import requests

from ninova_mcp.client import NinovaError
from ninova_mcp.portal_data import PortalUnreachableError, decode_portal_response, portal_call
from ninova_mcp.server import NinovaMcpApp

from recording import offline_app

PAGE = "https://portal.itu.edu.tr/apps/default/"
SERVICE = PAGE + "service/service.aspx/"
ROWS = [
    {"ObjectId": 501, "Title": "Example maintenance", "ContentText": "<p>Planned <b>work</b></p>",
     "CreateDate": "2041-03-04T10:00:00", "BeforeCreateDate": "2 s", "IsRead": 0},
    {"ObjectId": 502, "Title": "Example reply", "ContentText": "", "CreateDate": "2041-03-01T09:00:00",
     "BeforeCreateDate": "3 g", "IsRead": 1},
]
WIDGET = (
    '<ul data-placement="notification-list">'
    '<li class="notification__list-item notification__list-item--unread">'
    '<a href="javascript:void(0);" data-notification-id="601">'
    '<span class="pull-left">Example widget notice</span>'
    '<span class="pull-right">1 g</span></a></li></ul>'
)
JSON_TYPE = "application/json; charset=utf-8"


def fake_response(body: bytes, content_type: str = JSON_TYPE, status: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.headers["Content-Type"] = content_type
    response._content = body
    response._content_consumed = True
    response.encoding = "utf-8"
    return response


def app_with_mock_client() -> NinovaMcpApp:
    app = NinovaMcpApp()
    app._client = Mock()
    return app


class DecodeTests(unittest.TestCase):
    def decode(self, body: bytes, operation: str = "GetNotification", **kwargs) -> dict:
        return decode_portal_response(operation, fake_response(body, **kwargs))

    def test_plain_wrapped_object(self) -> None:
        self.assertEqual(self.decode(b'{"d": {"StatusCode": 0}}'), {"StatusCode": 0})

    def test_byte_order_mark_is_tolerated(self) -> None:
        body = b"\xef\xbb\xbf" + json.dumps({"d": {"NotificationInformationList": ROWS}}).encode()
        self.assertEqual(len(self.decode(body)["NotificationInformationList"]), 2)

    def test_json_text_inside_a_string_is_unwrapped(self) -> None:
        inner = json.dumps({"NotificationInformationList": ROWS})
        self.assertEqual(len(self.decode(json.dumps({"d": inner}).encode())["NotificationInformationList"]), 2)
        self.assertEqual(len(self.decode(json.dumps(inner).encode())["NotificationInformationList"]), 2)

    def test_top_level_array_is_placed_under_the_list_key(self) -> None:
        self.assertEqual(self.decode(json.dumps({"d": ROWS}).encode()), {"NotificationInformationList": ROWS})
        self.assertEqual(self.decode(json.dumps(ROWS).encode()), {"NotificationInformationList": ROWS})
        self.assertEqual(self.decode(json.dumps({"d": []}).encode(), "GetYardim"), {"YardimInformationList": []})

    def test_raw_control_characters_inside_strings_are_tolerated(self) -> None:
        body = b'{"d": {"NotificationInformationList": [{"ObjectId": 1, "Title": "Line one\nline\ttwo"}]}}'
        self.assertEqual(self.decode(body)["NotificationInformationList"][0]["ObjectId"], 1)

    def test_object_without_d_wrapper_is_accepted(self) -> None:
        self.assertEqual(self.decode(b'{"StatusCode": 0, "NotificationInformationList": []}')["StatusCode"], 0)

    def test_unparseable_body_error_names_status_type_and_prefix(self) -> None:
        html = b"<!DOCTYPE html><html><head><title>Example error</title></head><body>" + b"x" * 400 + b"</body></html>"
        with self.assertRaises(NinovaError) as caught:
            self.decode(html, content_type="text/html; charset=utf-8")
        message = str(caught.exception)
        self.assertIn("GetNotification", message)
        self.assertIn("HTTP 200", message)
        self.assertIn("text/html", message)
        self.assertIn("Example error", message)
        self.assertIn("HTML", message)
        self.assertLess(len(message), 700)

    def test_empty_body_is_named(self) -> None:
        for body in (b"", b"  \r\n", b"\xef\xbb\xbf"):
            with self.subTest(body=body), self.assertRaisesRegex(NinovaError, "empty body"):
                self.decode(body)

    def test_error_prefix_hides_addresses_and_long_numbers(self) -> None:
        with self.assertRaises(NinovaError) as caught:
            self.decode(b"oops ornek.kisi@example.edu 150990123456 end")
        message = str(caught.exception)
        self.assertNotIn("ornek.kisi@example.edu", message)
        self.assertNotIn("150990123456", message)
        self.assertIn("oops", message)

    def test_scalar_or_wrong_shape_is_refused(self) -> None:
        for body in (b'{"d": 5}', b'{"d": null}', b'"plain text"', b"12"):
            with self.subTest(body=body), self.assertRaises(NinovaError):
                self.decode(body)
        with self.assertRaises(NinovaError):
            self.decode(b'{"d": [1]}', "GetBalance")


class TransportShapeTests(unittest.TestCase):
    """The same shapes through the real request path, answered by a recorder."""

    def test_notifications_read_from_a_bom_prefixed_string_wrapped_reply(self) -> None:
        inner = json.dumps({"StatusCode": 0, "NotificationInformationList": ROWS})
        body = b"\xef\xbb\xbf" + json.dumps({"d": inner}).encode()
        app, adapter = offline_app(self, {SERVICE + "GetNotification": (200, {"Content-Type": JSON_TYPE}, body)})
        result = app.obs_get_notifications()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["notifications"][0]["content"], "Planned work")
        self.assertNotIn("api_fallback", result)
        self.assertEqual(set(adapter.hosts), {"portal.itu.edu.tr"})

    def test_html_reply_and_empty_widget_is_unavailable_with_capture_facts(self) -> None:
        reply = (200, {"Content-Type": "text/html; charset=utf-8"}, b"<html><body>Example failure page</body></html>")
        app, _ = offline_app(self, {SERVICE + "GetNotification": reply})
        result = app.obs_get_notifications()
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["count"])
        self.assertIsNone(result["notifications"])
        self.assertIn("HTTP 200", result["api_error"])
        self.assertIn("text/html", result["api_error"])
        self.assertIn("Example failure page", result["api_error"])


class NotificationToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = app_with_mock_client()

    def call(self, api: object, html: str = "<html></html>", **kwargs) -> dict:
        api_kwargs = {"side_effect": api} if isinstance(api, Exception) else {"return_value": api}
        with patch.object(self.app, "_get_portal_json", **api_kwargs), \
                patch.object(self.app, "_get_portal_page", return_value=(html, PAGE)):
            return self.app.obs_get_notifications(**kwargs)

    def test_failed_api_and_empty_fallback_is_unavailable_not_empty(self) -> None:
        result = self.call(NinovaError("Portal GetNotification JSON yanıtı ayrıştırılamadı."))
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["count"])
        self.assertIsNone(result["notifications"])
        self.assertTrue(result["api_fallback"])
        self.assertTrue(result["reason"])
        self.assertIn("ayrıştırılamadı", result["api_error"])
        self.assertTrue(result["untrusted_external_content"])

    def test_fallback_items_are_marked_as_fallback(self) -> None:
        result = self.call(NinovaError("unparseable"), WIDGET)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 1)
        self.assertTrue(result["api_fallback"])
        self.assertEqual(result["source_kind"], "homepage_html_fallback")
        self.assertEqual(result["notifications"][0]["title"], "Example widget notice")
        self.assertIn("unparseable", result["api_error"])

    def test_successful_api_read_with_no_items_is_a_real_zero(self) -> None:
        result = self.call({"StatusCode": 0, "NotificationInformationList": []})
        self.assertEqual((result["status"], result["count"], result["notifications"]), ("ok", 0, []))
        self.assertNotIn("api_fallback", result)

    def test_api_reply_without_a_list_or_with_failed_status_is_not_a_zero(self) -> None:
        for payload in ({}, {"StatusCode": 0}, {"StatusCode": 2, "NotificationInformationList": []},
                        {"StatusCode": 0, "NotificationInformationList": None},
                        {"StatusCode": 0, "NotificationInformationList": "none"}):
            with self.subTest(payload=payload):
                result = self.call(payload)
                self.assertEqual(result["status"], "unavailable")
                self.assertIsNone(result["count"])

    def test_api_items_and_limit(self) -> None:
        result = self.call({"StatusCode": 0, "NotificationInformationList": ROWS}, limit=1)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["notifications"][0]["id"], "501")
        self.assertTrue(result["notifications"][0]["unread"])

    def test_detail_request_fails_clearly_when_nothing_could_be_read(self) -> None:
        with self.assertRaisesRegex(NinovaError, "could not be read"):
            self.call(NinovaError("unparseable"), notification_id="501")
        payload = {"StatusCode": 0, "NotificationInformationList": ROWS}
        with self.assertRaisesRegex(NinovaError, "bulunamadı"):
            self.call(payload, notification_id="999")
        self.assertEqual(self.call(payload, notification_id="502")["notification"]["id"], "502")

    def test_unreachable_host_is_a_short_error_not_a_fallback(self) -> None:
        cause = requests.ConnectionError(
            "HTTPSConnectionPool(host='portal.itu.edu.tr', port=443): Max retries exceeded with url: /apps/default/ "
            "(Caused by NameResolutionError(\"<urllib3.connection.HTTPSConnection object at 0x0>: Failed to resolve "
            "'portal.itu.edu.tr' ([Errno 11001] getaddrinfo failed)\"))")
        error = NinovaError(f"ITU request failed safely: {cause}")
        error.__cause__ = cause
        with patch.object(self.app, "_get_portal_json", side_effect=error), \
                patch.object(self.app, "_get_portal_page") as page:
            for tool in (self.app.obs_get_notifications, self.app.obs_get_campus_card,
                         self.app.obs_get_cloud_quota, self.app.obs_get_help_tickets):
                with self.subTest(tool=tool.__name__), self.assertRaises(PortalUnreachableError) as caught:
                    tool()
                message = str(caught.exception)
                self.assertIn("portal.itu.edu.tr", message)
                self.assertIn("try again", message)
                self.assertNotIn("getaddrinfo", message)
                self.assertNotIn("NameResolutionError", message)
        page.assert_not_called()

    def test_portal_call_passes_other_errors_through(self) -> None:
        with self.assertRaisesRegex(NinovaError, "plain failure") as caught:
            portal_call(Mock(side_effect=NinovaError("plain failure")), "GetBalance")
        self.assertNotIsInstance(caught.exception, PortalUnreachableError)
        self.assertEqual(portal_call(Mock(return_value={"a": 1}), "GetBalance"), {"a": 1})


if __name__ == "__main__":
    unittest.main()
