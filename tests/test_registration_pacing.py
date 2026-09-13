"""Check OBS validation spacing and bounded busy retries without real delays."""

from __future__ import annotations

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, call, patch

import requests

from ninova_mcp.obs_client import ObsClient, ObsError
from ninova_mcp.registration_draft import VALIDATION_PATH


def success(crn: str = "10001") -> dict:
    return {"statusCode": 0, "ecrnResultList": [{
        "crn": crn, "statusCode": 0, "dersKodu": "BLG 201",
        "dersAdi": "Example Course", "kredi": 3, "taslakKontrolResultList": [],
    }]}


class Clock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


class RegistrationPacingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ObsClient(ninova_client=Mock(), base_url="https://obs.itu.edu.tr")
        self.clock = Clock()
        self.monotonic = patch("ninova_mcp.obs_client.time.monotonic", self.clock.monotonic)
        self.sleep = patch("ninova_mcp.obs_client.time.sleep", self.clock.sleep)
        self.monotonic.start()
        self.sleep.start()
        self.addCleanup(self.monotonic.stop)
        self.addCleanup(self.sleep.stop)

    def test_consecutive_checks_wait_from_previous_completion(self) -> None:
        starts = []

        def request(*args, **kwargs):
            starts.append(self.clock.now)
            self.clock.now += 0.75
            return success()

        with patch.object(self.client, "_api_request", side_effect=request) as api:
            first = self.client.validate_registration_crns(["10001"])
            second = self.client.validate_registration_crns(["10001"])
        self.assertTrue(first["10001"]["eligible"])
        self.assertTrue(second["10001"]["eligible"])
        self.assertEqual(starts, [100.0, 102.75])
        self.assertEqual(self.clock.sleeps, [2.0])
        self.assertEqual(api.call_args_list, [call("POST", VALIDATION_PATH, json_body={"ecrn": ["10001"]})] * 2)

    def test_elapsed_interval_needs_no_extra_delay(self) -> None:
        with patch.object(self.client, "_api_request", return_value=success()):
            self.client.validate_registration_crns(["10001"])
            self.clock.now += 3
            self.client.validate_registration_crns(["10001"])
        self.assertEqual(self.clock.sleeps, [])

    def test_known_busy_responses_retry_once_after_spacing(self) -> None:
        for code in ("VAL16", "ERRMaxIstekTaslakKontrol"):
            with self.subTest(code=code):
                client = ObsClient(ninova_client=Mock(), base_url="https://obs.itu.edu.tr")
                self.clock.sleeps.clear()
                with patch.object(client, "_api_request", side_effect=[{"statusCode": 1, "resultCode": code}, success()]) as api:
                    self.assertTrue(client.validate_registration_crns(["10001"])["10001"]["eligible"])
                self.assertEqual(api.call_count, 2)
                self.assertEqual(self.clock.sleeps, [2.0])

    def test_persistent_busy_response_is_bounded(self) -> None:
        with patch.object(self.client, "_api_request", return_value={"statusCode": 1, "resultCode": "VAL16"}) as api:
            with self.assertRaises(ObsError):
                self.client.validate_registration_crns(["10001"])
        self.assertEqual(api.call_count, 2)
        self.assertEqual(self.clock.sleeps, [2.0])

    def test_non_busy_restrictions_are_not_retried(self) -> None:
        with patch.object(self.client, "_api_request", return_value={"statusCode": 1, "resultCode": "VAL08"}) as api:
            with self.assertRaises(ObsError):
                self.client.validate_registration_crns(["10001"])
        self.assertEqual(api.call_count, 1)
        self.assertEqual(self.clock.sleeps, [])

    def test_request_failure_still_spaces_the_next_check(self) -> None:
        with patch.object(self.client, "_api_request", side_effect=[requests.Timeout("Synthetic timeout."), success()]) as api:
            with self.assertRaises(requests.Timeout):
                self.client.validate_registration_crns(["10001"])
            self.client.validate_registration_crns(["10001"])
        self.assertEqual(api.call_count, 2)
        self.assertEqual(self.clock.sleeps, [2.0])

    def test_positive_status_without_a_recognizable_course_is_not_eligible(self) -> None:
        for code in (None, "", "Unexpected course content"):
            with self.subTest(code=code):
                client = ObsClient(ninova_client=Mock(), base_url="https://obs.itu.edu.tr")
                payload = success()
                payload["ecrnResultList"][0]["dersKodu"] = code
                with patch.object(client, "_api_request", return_value=payload) as api:
                    result = client.validate_registration_crns(["10001"])
                self.assertIsNone(result["10001"]["eligible"])
                self.assertEqual(api.call_count, 1)

    def test_unrecognized_business_code_is_an_error_without_a_retry(self) -> None:
        with patch.object(self.client, "_api_request", return_value={"statusCode": 1, "resultCode": ["VAL16"]}) as api:
            with self.assertRaises(ObsError):
                self.client.validate_registration_crns(["10001"])
        self.assertEqual(api.call_count, 1)
        self.assertEqual(self.clock.sleeps, [])

    def test_invalid_input_neither_waits_nor_calls_obs(self) -> None:
        self.client._last_registration_check = self.clock.now
        with patch.object(self.client, "_api_request") as api:
            with self.assertRaises(ObsError):
                self.client.validate_registration_crns([])
        api.assert_not_called()
        self.assertEqual(self.clock.sleeps, [])

    def test_concurrent_calls_on_one_client_are_serialized(self) -> None:
        first_entered = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        second_entered = threading.Event()
        calls = []

        def request(*args, **kwargs):
            crn = kwargs["json_body"]["ecrn"][0]
            calls.append(crn)
            if crn == "10001":
                first_entered.set()
                if not release_first.wait(timeout=2):
                    raise AssertionError("The first synthetic request was not released.")
            else:
                second_entered.set()
            return success(crn)

        def second_call():
            second_started.set()
            return self.client.validate_registration_crns(["10002"])

        with patch.object(self.client, "_api_request", side_effect=request):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(self.client.validate_registration_crns, ["10001"])
                try:
                    self.assertTrue(first_entered.wait(timeout=1))
                    second = executor.submit(second_call)
                    self.assertTrue(second_started.wait(timeout=1))
                    self.assertFalse(second_entered.wait(timeout=0.05))
                finally:
                    release_first.set()
                self.assertTrue(first.result(timeout=2)["10001"]["eligible"])
                self.assertTrue(second.result(timeout=2)["10002"]["eligible"])
        self.assertEqual(calls, ["10001", "10002"])
        self.assertEqual(self.clock.sleeps, [2.0])


if __name__ == "__main__":
    unittest.main()
