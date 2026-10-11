"""Quota numbers need a unit and card amounts need a label."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from ninova_mcp.portal_data import campus_card, storage_quota

from test_portal_data import balance_payload, quota_payload


def quota(used: object, total: object, percent: str = "1.1") -> dict:
    payload = quota_payload(percent)
    payload["KotaInformation"].update({"KotaKullanilan": used, "KotaTotal": total})
    return payload


class QuotaUnitTests(unittest.TestCase):
    def read(self, mail: dict, cloud: dict) -> dict:
        return storage_quota(Mock(side_effect=[mail, cloud]))

    def test_stated_units_become_numbers_and_bytes(self) -> None:
        cloud = self.read(quota("0,25", "32"), quota("24 MB", "4 GB", "0.6"))["cloud"]
        self.assertEqual((cloud["used"], cloud["total"]), ("24 MB", "4 GB"))
        self.assertEqual((cloud["used_value"], cloud["used_unit"]), (24.0, "MB"))
        self.assertEqual((cloud["total_value"], cloud["total_unit"]), (4.0, "GB"))
        self.assertEqual(cloud["used_bytes"], 24 * 1024 ** 2)
        self.assertEqual(cloud["total_bytes"], 4 * 1024 ** 3)
        self.assertEqual(cloud["usage_percent_value"], 0.6)
        self.assertIsNone(cloud["unit_note"])
        self.assertEqual(cloud["details"], "Storage used: 24 MB / 4 GB")

    def test_unitless_mail_quota_says_the_unit_is_assumed(self) -> None:
        mail = self.read(quota("0,25", "32"), quota("24 MB", "4 GB"))["mail"]
        self.assertEqual((mail["used"], mail["total"]), ("0,25", "32"))
        self.assertEqual((mail["used_value"], mail["total_value"]), (0.25, 32.0))
        self.assertIsNone(mail["used_unit"])
        self.assertIsNone(mail["total_unit"])
        self.assertEqual(mail["assumed_unit"], "GB")
        self.assertIsNone(mail["used_bytes"])
        self.assertIn("does not state a unit", mail["unit_note"])
        self.assertIn("GB", mail["details"])
        self.assertIn("assumed", mail["details"])
        self.assertTrue(mail["available"])

    def test_unitless_cloud_quota_gets_no_assumed_unit(self) -> None:
        cloud = self.read(quota("0,25", "32"), quota("3", "8"))["cloud"]
        self.assertIsNone(cloud["assumed_unit"])
        self.assertIn("does not state a unit", cloud["unit_note"])
        self.assertEqual(cloud["details"], "Storage used: 3 / 8 (unit not stated)")

    def test_decimal_forms_and_unreadable_values(self) -> None:
        mail = self.read(quota("1.25 GB", "10 GB"), quota("1,5 gb", "2 TB"))
        self.assertEqual(mail["mail"]["used_value"], 1.25)
        self.assertEqual((mail["cloud"]["used_value"], mail["cloud"]["used_unit"]), (1.5, "GB"))
        self.assertEqual(mail["cloud"]["total_bytes"], 2 * 1024 ** 4)
        odd = self.read(quota("about half", "10 GB"), quota(5, 10))
        self.assertIsNone(odd["mail"]["used_value"])
        self.assertEqual(odd["mail"]["used"], "about half")
        self.assertEqual(odd["cloud"]["used_value"], 5.0)

    def test_unavailable_quota_has_no_invented_numbers(self) -> None:
        mail = self.read({"StatusCode": 1}, quota("24 MB", "4 GB"))["mail"]
        self.assertFalse(mail["available"])
        self.assertNotIn("used_value", mail)


class CardAmountTests(unittest.TestCase):
    def test_transaction_amounts_are_labelled(self) -> None:
        result = campus_card(balance_payload())
        first, second = result["transactions"]
        self.assertEqual((first["balance_after"], first["amount"]), ("₺ 20,75", "₺ 5,25"))
        self.assertEqual((second["balance_after"], second["amount"]), ("₺ 26", "₺ 10"))
        self.assertEqual(first["amounts"], ["₺ 20,75", "₺ 5,25"])
        self.assertIn("balance_after", result["transaction_fields_note"])

    def test_missing_side_is_none_not_shifted(self) -> None:
        payload = balance_payload()
        payload["BalanceInformation"]["Transitions"][0].pop("Balance")
        first = campus_card(payload)["transactions"][0]
        self.assertIsNone(first["balance_after"])
        self.assertEqual(first["amount"], "₺ 5,25")


if __name__ == "__main__":
    unittest.main()
