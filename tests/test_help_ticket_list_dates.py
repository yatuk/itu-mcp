"""The ticket list carries one date of unstated meaning. It is not an age."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ninova_mcp import help_tickets
from ninova_mcp.help_tickets import summary
from ninova_mcp.server import NinovaMcpApp
from ninova_mcp.tool_metadata.portal import PORTAL_TOOLS

from test_help_tickets import ID, URL, detail_html

# Synthetic list row in the shape the Portal widget endpoint returns.
ROW = {"URL": URL, "Title": "Example document request", "TicketStateName": "Yeni", "BeforeCreateDate": "09.03.2041"}


class SummaryDateTests(unittest.TestCase):
    def test_absolute_list_date_is_not_called_an_age(self) -> None:
        item = summary(ROW)
        self.assertIsNone(item["age"])
        self.assertEqual(item["list_date"], "2041-03-09")
        self.assertEqual(item["list_date_raw"], "09.03.2041")
        self.assertIn("creation", item["list_date_note"])
        self.assertIsNone(item["created_at"])
        self.assertIsNone(item["updated_at"])

    def test_dotted_dates_are_day_first_and_slash_dates_month_first(self) -> None:
        self.assertEqual(summary({**ROW, "BeforeCreateDate": "02.11.2041"})["list_date"], "2041-11-02")
        self.assertEqual(summary({**ROW, "BeforeCreateDate": "11/02/2041"})["list_date"], "2041-11-02")
        self.assertEqual(summary({**ROW, "BeforeCreateDate": "09.03.2041 14:05"})["list_date"], "2041-03-09T14:05:00")

    def test_a_real_relative_age_stays_an_age(self) -> None:
        for raw in ("2 dk", "4 g", "Today"):
            with self.subTest(raw=raw):
                item = summary({**ROW, "BeforeCreateDate": raw})
                self.assertEqual(item["age"], raw)
                self.assertIsNone(item["list_date"])
                self.assertIsNone(item["list_date_raw"])
                self.assertNotIn("list_date_note", item)

    def test_row_without_any_date(self) -> None:
        item = summary({"URL": URL, "Title": "Example"})
        self.assertIsNone(item["age"])
        self.assertIsNone(item["list_date"])


class ListToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = NinovaMcpApp()
        self.app._client = Mock()
        self.app._client.get_help_desk_html.return_value = (
            detail_html([("Example Student", "05.03.2041 12:00", "Please provide a document.")]),
            SimpleNamespace(url=URL),
        )

    def listing(self, rows: list[dict], **kwargs) -> dict:
        with patch.object(self.app, "_get_portal_json", return_value={"YardimInformationList": rows}):
            return self.app.obs_get_help_tickets(**kwargs)

    def test_quick_read_reports_list_gaps_as_not_in_list_view(self) -> None:
        result = self.listing([dict(ROW)])
        item = result["tickets"][0]
        self.assertEqual(item["missing_fields"], [])
        self.assertEqual(item["not_in_list_view"], list(help_tickets.LIST_VIEW_ABSENT_FIELDS))
        self.assertIn("created_at", item["not_in_list_view"])
        self.assertIsNone(item["created_at"])
        self.assertEqual(item["list_date"], "2041-03-09")
        self.assertFalse(result["metadata_complete"])
        self.assertIn("include_details=true", result["full_fields_hint"])
        self.assertIn("get_help_ticket", result["full_fields_hint"])
        self.app._client.get_help_desk_html.assert_not_called()

    def test_a_field_the_list_should_carry_is_still_missing(self) -> None:
        item = self.listing([{"URL": URL, "TicketStateName": "Yeni"}])["tickets"][0]
        self.assertEqual(item["missing_fields"], ["title"])

    def test_detail_read_fills_creation_date_and_keeps_the_list_date_apart(self) -> None:
        result = self.listing([dict(ROW)], include_details=True)
        item = result["tickets"][0]
        self.assertEqual(item["created_at"], "2041-03-05T12:00:00")
        self.assertEqual(item["list_date"], "2041-03-09")
        self.assertEqual(item["not_in_list_view"], [])
        self.assertEqual(item["missing_fields"], [])
        self.assertTrue(result["metadata_complete"])
        self.assertNotIn("full_fields_hint", result)

    def test_failed_detail_read_keeps_list_gaps_labelled(self) -> None:
        from ninova_mcp.client import NinovaError

        self.app._client.get_help_desk_html.side_effect = NinovaError("Unavailable")
        item = self.listing([dict(ROW)], include_details=True)["tickets"][0]
        self.assertFalse(item["detail_available"])
        self.assertIn("created_at", item["not_in_list_view"])
        self.assertEqual(item["missing_fields"], [])

    def test_description_and_schema_default_agree(self) -> None:
        tool = next(tool for tool in PORTAL_TOOLS if tool["name"] == "obs_get_help_tickets")
        self.assertIs(tool["inputSchema"]["properties"]["include_details"]["default"], False)
        description = tool["description"]
        self.assertNotIn("enriched from the help desk by default", description)
        self.assertNotIn("Set include_details=false", description)
        self.assertIn("include_details=true", description)
        self.assertIn("list_date", description)
        self.assertIn("not_in_list_view", description)


if __name__ == "__main__":
    unittest.main()
