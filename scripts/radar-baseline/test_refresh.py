import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import refresh

NOW = dt.datetime(2026, 10, 5, tzinfo=dt.timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures/moscow-baseline.html"


class BaselineTest(unittest.TestCase):
    def test_whitelists_public_minima_and_keeps_expiry_and_provenance(self):
        result = refresh.extract(FIXTURE.read_bytes(), NOW).decode()
        self.assertIn("06.10.2026", result)
        self.assertIn("checked_utc=2026-10-05", result)
        self.assertIn("source=" + refresh.SOURCE, result)
        self.assertNotIn("call_center", result)
        self.assertNotIn("name", result)
        payload = result.split(refresh.MARKER)[1].split(");</script>")[0]
        tariffs = json.loads(payload)["initialState"]["zonaltariffdescription"]["max_tariffs"]
        self.assertEqual(9, len(tariffs))
        values = {t["id"]: t["intervals"][0]["price_groups"][0]["prices"][0]["price"] for t in tariffs}
        self.assertEqual("189", values["econom"])
        self.assertEqual("219", values["business"])
        self.assertEqual("269", values["comfortplus"])

    def test_invalid_sources_fail_closed(self):
        source = FIXTURE.read_bytes()
        variants = [source.replace(b"06.10.2026", b"04.10.2026"),
                    source.replace(b'"moscow"', b'"kazan"'),
                    source.replace(b'"RUB"', b'"USD"'),
                    source.replace(b'"isZoneUnsupported": false', b'"isZoneUnsupported": true'),
                    source.replace(b'"business"', b'"unknown"'),
                    source.replace(b'"189', b'"189.000000000000000000000000000001'),
                    source.replace(b'"189', b'"0'),
                    source.replace(b'"code": "RUB"', b'"code": "RUB", "code": "RUB"'),
                    source + b'<p data-key="tariff.disclaimer.expiry.date">06.10.2026.</p>',
                    source + refresh.MARKER.encode(), b"x" * (refresh.LIMIT + 1), source[:-8]]
        for raw in variants:
            with self.subTest(raw_length=len(raw)), self.assertRaises((ValueError, KeyError)):
                refresh.extract(raw, NOW)

    def test_failed_fetch_preserves_published_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "baseline.html"
            target.write_bytes(b"previous")
            with patch("refresh.urllib.request.build_opener", side_effect=OSError("offline")):
                with self.assertRaises(OSError):
                    refresh.refresh(target)
            self.assertEqual(b"previous", target.read_bytes())

    def test_redirects_are_rejected(self):
        with self.assertRaises(ValueError):
            refresh.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")

    def test_invalid_response_preserves_published_file(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b"invalid source"
        response.headers = {}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "baseline.html"
            target.write_bytes(b"previous")
            with patch("refresh.urllib.request.build_opener") as opener:
                opener.return_value.open.return_value = response
                with self.assertRaises(ValueError):
                    refresh.refresh(target)
            self.assertEqual(b"previous", target.read_bytes())
            self.assertFalse(target.with_suffix(".tmp").exists())


if __name__ == "__main__":
    unittest.main()
