"""Automated Test Suite for Resilience Engine & Security Guards.

Validates capacity calculation, fail-safe range math, and SSRF host allowlisting.
"""
import unittest
from src.resilience.sheet_write_safety import column_number, column_label
from src.resilience.logistics_adapter import _is_host_allowed
from src.resilience.gspread_resilience import _TRANSIENT_STATUS_CODES, _QUOTA_REASONS


class TestResilienceEngine(unittest.TestCase):

    def test_google_sheet_column_conversions(self):
        """Validates bijective base-26 column coordinate conversions."""
        self.assertEqual(column_number("A"), 1)
        self.assertEqual(column_number("Z"), 26)
        self.assertEqual(column_number("AA"), 27)
        self.assertEqual(column_number("AZ"), 52)
        self.assertEqual(column_number("BA"), 53)

        self.assertEqual(column_label(1), "A")
        self.assertEqual(column_label(26), "Z")
        self.assertEqual(column_label(27), "AA")
        self.assertEqual(column_label(52), "AZ")
        self.assertEqual(column_label(53), "BA")

    def test_ssrf_host_allowlist_guard(self):
        """Guarantees malicious internal or rogue domains are blocked before dispatch."""
        self.assertTrue(_is_host_allowed("https://services.giaohangtietkiem.vn"))
        self.assertTrue(_is_host_allowed("https://api.giaohangtietkiem.vn"))

        # Malicious SSRF attempts
        self.assertFalse(_is_host_allowed("http://169.254.169.254/latest/meta-data"))
        self.assertFalse(_is_host_allowed("http://localhost:8080"))
        self.assertFalse(_is_host_allowed("https://attacker-domain.com"))
        self.assertFalse(_is_host_allowed("https://evil-giaohangtietkiem.vn.fake.com"))
        self.assertFalse(_is_host_allowed(""))

    def test_transient_error_classification(self):
        """Ensures transient quota and network errors are properly targeted for retry."""
        self.assertIn(429, _TRANSIENT_STATUS_CODES)  # Rate limit exceeded
        self.assertIn(503, _TRANSIENT_STATUS_CODES)  # Service unavailable
        self.assertIn(502, _TRANSIENT_STATUS_CODES)  # Bad gateway

        self.assertIn("rateLimitExceeded", _QUOTA_REASONS)
        self.assertIn("userRateLimitExceeded", _QUOTA_REASONS)


if __name__ == "__main__":
    unittest.main()
