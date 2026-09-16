"""Regression coverage for asynchronously populated Portal widgets."""

import unittest
from unittest.mock import Mock, patch

from ninova_mcp.client import NinovaError
from ninova_mcp.portal_data import campus_card, storage_quota
from ninova_mcp.server import NinovaMcpApp


def balance_payload():
    return {"StatusCode": 0, "BalanceInformation": {
        "Balance": "20,75", "ITUNumber": "private-number",
        "BalanceGetDate": "2026-09-13", "Transitions": [
            {"TypeId": 1, "TypeName": "Lunch", "Balance": "20,75", "TransactionAmount": "5,25"},
            {"TypeId": 2, "TypeName": "Top up", "Balance": "26", "TransactionAmount": "10"},
        ],
    }}


def quota_payload(percent="12.5"):
    return {"StatusCode": 0, "KotaInformation": {
        "KotaYuzde": percent, "KotaKullanilan": "1.25 GB", "KotaTotal": "10 GB",
        "PersonPublicId": "private-id", "KotaGetDate": "2026-09-13",
    }}


class PortalDataTests(unittest.TestCase):
    def test_card_reads_widget_json_and_omits_identifiers(self):
        result = campus_card(balance_payload())
        self.assertEqual(result["balance"], "₺ 20,75")
        self.assertEqual(result["transaction_count"], 2)
        self.assertEqual([row["type"] for row in result["transactions"]], ["spending", "credit"])
        self.assertNotIn("private-number", str(result))
        self.assertTrue(result["untrusted_external_content"])

    def test_zero_balance_is_valid(self):
        payload = balance_payload()
        payload["BalanceInformation"]["Balance"] = 0
        self.assertEqual(campus_card(payload)["balance"], "₺ 0")

    def test_failed_missing_or_empty_balance_is_not_success(self):
        for payload in ({}, {"StatusCode": 2}, {"StatusCode": False},
                        {"StatusCode": 0, "BalanceInformation": {}},
                        {"StatusCode": 0, "BalanceInformation": {"Balance": " "}}):
            with self.subTest(payload=payload), self.assertRaises(NinovaError):
                campus_card(payload)

    def test_missing_history_does_not_mean_zero_transactions(self):
        payload = balance_payload()
        payload["BalanceInformation"]["Transitions"] = None
        result = campus_card(payload)
        self.assertIsNone(result["transaction_count"])
        self.assertFalse(result["transactions_available"])
        self.assertEqual(result["balance"], "₺ 20,75")

    def test_card_transaction_limit_keeps_total_count(self):
        payload = balance_payload()
        payload["BalanceInformation"]["Transitions"] *= 15
        result = campus_card(payload)
        self.assertEqual(result["transaction_count"], 30)
        self.assertEqual(len(result["transactions"]), 20)

    def test_string_type_id_matches_portal_javascript(self):
        payload = balance_payload()
        payload["BalanceInformation"]["Transitions"][0]["TypeId"] = "20"
        self.assertEqual(campus_card(payload)["transactions"][0]["type"], "spending")

    def test_quota_reads_both_services_without_account_ids(self):
        get_json = Mock(side_effect=[quota_payload(), quota_payload("0")])
        result = storage_quota(get_json)
        self.assertEqual([call.args[0] for call in get_json.call_args_list], ["GetQuota", "GetQuotaEski"])
        self.assertEqual(result["mail"]["usage_percent"], "%12.5")
        self.assertEqual(result["cloud"]["usage_percent"], "%0")
        self.assertEqual(result["mail"]["details"], "Storage used: 1.25 GB / 10 GB")
        self.assertTrue(result["mail"]["available"])
        self.assertNotIn("private-id", str(result))

    def test_one_quota_failure_preserves_the_other(self):
        result = storage_quota(Mock(side_effect=[NinovaError("private response"), quota_payload()]))
        self.assertFalse(result["mail"]["available"])
        self.assertIsNone(result["mail"]["usage_percent"])
        self.assertTrue(result["cloud"]["available"])
        self.assertNotIn("private response", str(result))

    def test_failed_business_status_is_not_a_zero_quota(self):
        result = storage_quota(Mock(return_value={"StatusCode": 1}))
        self.assertTrue(all(not result[key]["available"] for key in ("mail", "cloud")))
        self.assertIsNone(result["mail"]["usage_percent"])

    def test_missing_quota_field_is_explicitly_incomplete(self):
        payload = quota_payload()
        payload["KotaInformation"]["KotaYuzde"] = ""
        result = storage_quota(Mock(return_value=payload))
        self.assertFalse(result["mail"]["available"])
        self.assertIsNone(result["mail"]["usage_percent"])
        self.assertIn("error", result["mail"])

    def test_tools_use_live_widget_endpoints_instead_of_empty_html(self):
        app = NinovaMcpApp()
        with patch.object(app, "_get_portal_json", side_effect=[balance_payload(), quota_payload(), quota_payload()]) as get_json:
            self.assertEqual(app.obs_get_campus_card()["balance"], "₺ 20,75")
            self.assertTrue(app.obs_get_cloud_quota()["mail"]["available"])
        self.assertEqual([call.args[0] for call in get_json.call_args_list], ["GetBalance", "GetQuota", "GetQuotaEski"])


if __name__ == "__main__":
    unittest.main()
