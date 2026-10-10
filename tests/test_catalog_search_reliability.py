"""Regression tests for the actual OBS course information form."""

import unittest
from unittest.mock import Mock, PropertyMock, patch

from ninova_mcp.obs_client import ObsError, ObsPublicClient
from ninova_mcp.parsing import extract_course_search_results
from ninova_mcp.server import NinovaMcpApp


CATALOG = """
<div><table><tbody><tr><td>
  <table><thead><tr><th>Ders Kodu</th><th>Ders Adı</th><th>Dil</th></tr></thead>
    <tbody><tr><td>UZB438 -UZB438E</td><td>Robotic Control Systems</td><td>English/Turkish</td></tr></tbody>
  </table>
  <table><tr><td>Credit</td><td>ECTS</td></tr><tr><td>3</td><td>5</td></tr></table>
  <table><tr><td>Prerequisites</td></tr><tr><td>UCK 362E MIN. DD</td></tr></table>
</td></tr></tbody></table></div>
"""


class CatalogSearchReliabilityTests(unittest.TestCase):
    def test_nested_layout_does_not_mix_credit_or_prerequisite_rows_into_courses(self):
        result = extract_course_search_results(CATALOG, "https://obs.itu.edu.tr/public/DersBilgi/DersBilgiSearch")
        self.assertEqual([row["code"] for row in result], ["UZB 438", "UZB 438E"])
        self.assertEqual([row["name"] for row in result], ["Robotic Control Systems"] * 2)

    def test_lookup_uses_real_route_parameters_and_ranks_exact_language_variant(self):
        client = ObsPublicClient()
        with patch.object(client, "_get_html", return_value=(CATALOG, "https://obs.itu.edu.tr/result")) as get_html:
            result = client.search_courses("uzb438e")
        get_html.assert_called_once_with("/public/DersBilgi/DersBilgiSearch", params={"bransKodu": "UZB", "dersNo": "438E"})
        self.assertEqual(result[0]["code"], "UZB 438E")

    def test_four_digit_capstone_number_is_not_truncated(self):
        client = ObsPublicClient()
        html = CATALOG.replace("UZB438", "UZB4901")
        with patch.object(client, "_get_html", return_value=(html, "https://obs.itu.edu.tr/result")) as get_html:
            result = client.search_courses("UZB 4901E")
        self.assertEqual(get_html.call_args.kwargs["params"]["dersNo"], "4901E")
        self.assertEqual(result[0]["code"], "UZB 4901E")

    def test_unrecognized_html_is_not_a_successful_empty_search(self):
        client = ObsPublicClient()
        with patch.object(client, "_get_html", return_value=("<p>Service unavailable</p>", "https://obs.itu.edu.tr/result")):
            with self.assertRaises(ObsError):
                client.search_courses("UZB 438E")

    def test_exact_search_does_not_require_archive_or_credentials(self):
        app = NinovaMcpApp()
        app._obs_public = Mock()
        app._obs_public.search_courses.return_value = [{"code": "UZB 438E", "name": "Robotic Control Systems"}]
        with patch.object(type(app), "archive", new_callable=PropertyMock, side_effect=AssertionError("Archive must not be accessed")):
            result = app.obs_search_courses("UZB438E")
        self.assertEqual(result["source"], "official_obs_course_information")
        self.assertTrue(result["untrusted_external_content"])

    def test_name_search_keeps_compatibility_with_explicit_archive_source(self):
        app = NinovaMcpApp()
        app._archive = Mock(base_url="https://itu-ders.com/data")
        app._archive.get_course_codes.return_value = [
            ["UZB 438E", "Robotic Control Systems", "UZB", 4],
            ["UZB 419E", "Spacecraft Dynamics", "UZB", 5],
        ]
        result = app.obs_search_courses("Robotic")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["courses"][0]["code"], "UZB 438E")
        self.assertEqual(result["source"], "course_archive")
        self.assertIn("does not establish", result["offering_note"])

    def test_partial_code_search_honors_limit(self):
        app = NinovaMcpApp()
        app._archive = Mock(base_url="https://itu-ders.com/data")
        app._archive.get_course_codes.return_value = [["UZB 438E", "Control", "UZB", 4], ["UZB 419E", "Dynamics", "UZB", 5]]
        self.assertEqual(app.obs_search_courses("UZB", limit=1)["count"], 1)

    def test_empty_query_fails_before_network(self):
        app = NinovaMcpApp()
        with self.assertRaises(ObsError):
            app.obs_search_courses(" ")


if __name__ == "__main__":
    unittest.main()
