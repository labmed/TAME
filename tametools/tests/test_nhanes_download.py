from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from tametools.analysis import validate_dataset
from tametools.nhanes import download_nhanes, _base, _fetch, _OfficialRedirect, _content
from tametools.planned_analysis import analyze_dataset
from tametools.pipeline import execute_work
from test_clinical import fixture

XPT = b"HEADER RECORD*******LIBRARY HEADER RECORD".ljust(240, b" ")
HTML = b"<html><h2>2017-2018 Data Documentation, Codebook, and Frequencies</h2><p>DEMO_J.xpt</p></html>"


def fetch(url, base, timeout):
    return (XPT if url.endswith(".xpt") else HTML), url


class NhanesDownloadTests(unittest.TestCase):
    def download(self, root, **kwargs):
        return download_nhanes(root, cycle="2017-2018", files=["DEMO_J"], **kwargs)

    def test_download_cache_and_offline_checks(self):
        with tempfile.TemporaryDirectory() as tmp, patch("tametools.nhanes._fetch", side_effect=fetch) as network:
            root = Path(tmp)
            result = self.download(root)
            self.assertEqual(len(result["files"]), 2)
            before = (root / "source_manifest.json").read_bytes()
            result = self.download(root, offline=True)
            self.assertTrue(all(row["status"] == "verified_cache" for row in result["files"]))
            self.assertEqual(network.call_count, 2)
            self.assertEqual((root / "source_manifest.json").read_bytes(), before)
            self.assertFalse((root / ".nhanes-download.lock").exists())

    def test_corrupted_cache_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as tmp, patch("tametools.nhanes._fetch", side_effect=fetch) as network:
            root = Path(tmp)
            self.download(root)
            path = root / "sources/DEMO_J.xpt"
            path.write_bytes(XPT + b"changed")
            with self.assertRaisesRegex(ValueError, "checksum"):
                self.download(root)
            self.assertEqual(network.call_count, 2)
            self.assertTrue(path.read_bytes().endswith(b"changed"))

    def test_untracked_and_symlinked_sources_rejected(self):
        for kind in ["untracked", "symlink"]:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "sources").mkdir()
                target = root / "sources/DEMO_J.xpt"
                if kind == "untracked": target.write_bytes(XPT)
                else: target.symlink_to(root / "missing")
                with self.assertRaises(ValueError), patch("tametools.nhanes._fetch") as network:
                    self.download(root)
                network.assert_not_called()

    def test_offline_missing_and_lock_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, patch("tametools.nhanes._fetch") as network:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "Offline"):
                self.download(root, offline=True)
            lock = root / ".nhanes-download.lock"
            lock.touch()
            with self.assertRaisesRegex(ValueError, "locked"):
                self.download(root)
            self.assertTrue(lock.exists())
            network.assert_not_called()

    def test_resume_after_partial_network_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("tametools.nhanes._fetch", side_effect=[(XPT, _base("2017-2018")+"DEMO_J.xpt"), ValueError("network")]):
                with self.assertRaisesRegex(ValueError, "network"):
                    self.download(root)
            with patch("tametools.nhanes._fetch", side_effect=fetch) as network:
                self.download(root)
                self.assertEqual(network.call_count, 1)
                self.assertTrue(network.call_args.args[0].endswith(".htm"))

    def test_missing_cached_file_must_match_old_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp, patch("tametools.nhanes._fetch", side_effect=fetch):
            root = Path(tmp)
            self.download(root)
            target = root / "sources/DEMO_J.xpt"
            target.unlink()
            with patch("tametools.nhanes._fetch", return_value=(XPT+b" "*80, _base("2017-2018")+"DEMO_J.xpt")):
                with self.assertRaisesRegex(ValueError, "Remote source changed"):
                    self.download(root)
            self.assertFalse(target.exists())

    def test_pinned_expected_manifest(self):
        with tempfile.TemporaryDirectory() as tmp, patch("tametools.nhanes._fetch", side_effect=fetch):
            root = Path(tmp)
            pin = root / "expected.json"
            expected = {"DEMO_J.xpt": hashlib.sha256(XPT).hexdigest(), "DEMO_J.htm": hashlib.sha256(HTML).hexdigest()}
            pin.write_text(json.dumps(expected))
            self.download(root / "raw", expected_manifest=pin)
            expected["DEMO_J.xpt"] = "0"*64
            pin.write_text(json.dumps(expected))
            with self.assertRaisesRegex(ValueError, "pinned"):
                self.download(root / "raw", expected_manifest=pin)
            pin.write_text("{}")
            with self.assertRaisesRegex(ValueError, "pin every"):
                self.download(root / "missing", expected_manifest=pin)

    def test_content_and_codebook_cycle_are_checked(self):
        for data, name in [(b"<html>unavailable</html>", "DEMO_J.xpt"), (XPT[:100], "DEMO_J.xpt"),
                           (b"<html>error</html>", "DEMO_J.htm"), (HTML.replace(b"2017-2018", b"2015-2016"), "DEMO_J.htm"),
                           (HTML.replace(b"DEMO_J", b"BIOPRO_J"), "DEMO_J.htm")]:
            with self.subTest(name=name, data=data), self.assertRaises(ValueError):
                _content(data, name, "2017-2018")
        _content(HTML.replace(b"2017-2018", b"August 2021-August 2023"), "DEMO_J.htm", "2021-2023")

    def test_unsafe_and_unknown_identifiers_are_rejected(self):
        cases = [{"cycle":"2017-2020", "files":["DEMO_J"]}, {"cycle":"2017-2018", "files":["../DEMO_J"]},
                 {"cycle":"2017-2018", "files":["DEMO_J.xpt"]}, {"cycle":"2017-2018", "files":["DEMO_J", "DEMO_J"]}]
        for options in cases:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
                download_nhanes(tmp, **options)
        for timeout in [0, True, float("nan"), 301]:
            with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
                self.download(tmp, timeout=timeout)

    def test_manifest_cycle_and_url_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp, patch("tametools.nhanes._fetch", side_effect=fetch):
            root = Path(tmp)
            self.download(root)
            with self.assertRaisesRegex(ValueError, "cycle"):
                download_nhanes(root, cycle="2021-2023", files=["DEMO_L"], offline=True)
            manifest_path = root / "source_manifest.json"
            data = json.loads(manifest_path.read_text())
            data["files"]["DEMO_J.xpt"]["url"] = "https://example.com/DEMO_J.xpt"
            manifest_path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "URL"):
                self.download(root)

    def test_redirects_are_restricted_before_following(self):
        base = _base("2017-2018")
        handler = _OfficialRedirect(base)
        for url in ["http://wwwn.cdc.gov/x", "https://example.com/file", "https://wwwn.cdc.gov/other/file"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                handler.redirect_request(Request(base+"DEMO_J.xpt"), None, 302, "found", {}, url)

    def test_http_errors_retry_only_transient_failures(self):
        url, base = _base("2017-2018")+"DEMO_J.xpt", _base("2017-2018")
        for code, calls in [(404, 1), (503, 3)]:
            with patch("tametools.nhanes.build_opener") as opener, patch("tametools.nhanes.time.sleep"):
                opener.return_value.open.side_effect = HTTPError(url, code, "failed", {}, None)
                with self.assertRaisesRegex(ValueError, "download failed"):
                    _fetch(url, base, 1)
                self.assertEqual(opener.return_value.open.call_count, calls)
        for size, payload, message in [(3, b"12", "Incomplete"), (128*1024*1024+1, b"", "128 MiB")]:
            with self.subTest(size=size), patch("tametools.nhanes.build_opener") as opener:
                response = opener.return_value.open.return_value.__enter__.return_value
                response.url, response.headers = url, {"Content-Length": str(size)}
                response.read.return_value = payload
                with self.assertRaisesRegex(ValueError, message):
                    _fetch(url, base, 1)

    def test_manifest_write_failure_does_not_leave_untracked_source(self):
        with tempfile.TemporaryDirectory() as tmp, patch("tametools.nhanes._fetch", side_effect=fetch), patch("tametools.nhanes._json_atomic", side_effect=OSError("disk")):
            root = Path(tmp)
            with self.assertRaises(OSError):
                self.download(root)
            self.assertFalse((root / "sources/DEMO_J.xpt").exists())
            self.assertFalse((root / ".nhanes-download.lock").exists())

    def test_download_cli_offline_and_general_analysis_name(self):
        with tempfile.TemporaryDirectory() as tmp, patch("tametools.nhanes._fetch", side_effect=fetch):
            self.download(tmp)
            env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(Path(__file__).resolve().parents[1]/"src"), os.environ.get("PYTHONPATH", "")]))
            command = [sys.executable, "-m", "tametools", "download", "nhanes", "--cycle", "2017-2018", "--files", "DEMO_J", "--output-dir", tmp, "--offline"]
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("verified_cache", result.stdout)
            result = subprocess.run([sys.executable, "-m", "tametools", "analyze", "--help"], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("ANALYSIS_PLAN", result.stdout)
            result = subprocess.run([sys.executable, "-m", "tametools", "clinical", "--help"], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            for arguments, message in [(["CLINICAL_STATS", "--plan", "plan.toml"], "Do not combine"),
                                       (["--option", "MODE=anything"], "require a named PLUGIN")]:
                result = subprocess.run([sys.executable, "-m", "tametools", "analyze", "not_loaded.tame", *arguments], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)


class AnalysisMigrationTests(unittest.TestCase):
    def test_legacy_plan_and_workflow_preserve_observations(self):
        from tametools.clinical import clinical_analysis
        ds = fixture()
        ds.meta["CLINICAL_ANALYSIS"] = ds.meta.pop("ANALYSIS_PLAN")
        ds.meta["WORKS"]["DEFAULT"] = ["VALIDATE", "CLINICAL_ANALYSIS"]
        original = deepcopy(ds.meta)
        out = execute_work(ds).outputs[-1]
        self.assertEqual(out.name, "ANALYZE")
        self.assertIn("ANALYSIS_PLAN", out.dataset.meta)
        self.assertNotIn("CLINICAL_ANALYSIS", out.dataset.meta)
        self.assertEqual(ds.meta, original)
        self.assertEqual(clinical_analysis(ds).name, "ANALYZE")

    def test_mixed_or_case_duplicate_plans_are_errors(self):
        for key in ["CLINICAL_ANALYSIS", "analysis_plan"]:
            ds = fixture()
            ds.meta[key] = deepcopy(ds.meta["ANALYSIS_PLAN"])
            with self.assertRaisesRegex(ValueError, "Declare only"):
                analyze_dataset(ds)
            with self.assertRaisesRegex(ValueError, "Declare only"):
                analyze_dataset(ds, deepcopy(ds.meta["ANALYSIS_PLAN"]))
            self.assertTrue(any(issue.tag == "ANALYSIS_PLAN" for issue in validate_dataset(ds).issues))


if __name__ == "__main__":
    unittest.main()
