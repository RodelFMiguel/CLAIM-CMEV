"""Offline acquisition tests: no public datasets, credentials, or external clients needed."""
import argparse
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request
import zipfile

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/download_datasets.py"
SPEC = importlib.util.spec_from_file_location("download_datasets", SCRIPT)
download = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(download)


def archive_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("train/labels.json", '{"fixture": true}')
    return buffer.getvalue()


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None, fail_after=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}
        self.fail_after = fail_after
        self.calls = 0

    def read(self, size=-1):
        self.calls += 1
        if self.fail_after and self.calls > 1:
            raise OSError("simulated connection loss")
        return super().read(self.fail_after or size)


class DownloadsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.body = archive_bytes()
        self.source = self.root / "source.zip"
        self.source.write_bytes(self.body)
        self.output = self.root / "output"
        self.output.mkdir()
        self.args = argparse.Namespace(docile_extras=False, retries=0, timeout=1)

    def test_local_import_and_idempotent_integrity_check(self):
        spec = {"name": "original.zip", "path": str(self.source)}
        receipt = download.acquire_file(spec, self.output, 0, 1)
        self.assertEqual(receipt["sha256"], hashlib.sha256(self.body).hexdigest())
        self.assertEqual(download.acquire_file(spec, self.output, 0, 1), receipt)
        self.assertEqual(self.source.read_bytes(), self.body)
        (self.output / "original.zip").write_bytes(b"corrupted")
        with self.assertRaises(download.DownloadError):
            download.acquire_file(spec, self.output, 0, 1)

    def test_checksum_failure_never_publishes_completed_file(self):
        with self.assertRaises(download.DownloadError):
            download.acquire_file({"name": "original.zip", "path": str(self.source),
                                   "sha256": "0" * 64}, self.output, 0, 1)
        self.assertFalse((self.output / "original.zip").exists())
        self.assertFalse((self.output / "original.zip.receipt.json").exists())

    def test_rejects_html_bad_zip_and_empty_downloads(self):
        for payload in (b"<html>login</html>", b"bad zip", b""):
            path = self.output / "bad.zip.part"
            path.write_bytes(payload)
            with self.assertRaises(download.DownloadError):
                download.validate_file(path)

    def test_rejects_traversal_and_symlinks(self):
        for name in ("../outside.zip", "/absolute.zip", "C:/outside.zip", "a\\b.zip"):
            with self.assertRaises(download.DownloadError):
                download.safe_path(self.output, name)
        try:
            (self.output / "linked").symlink_to(self.root, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(download.DownloadError):
            download.safe_path(self.output, "linked/file.zip")

    def test_partial_symlink_and_reserved_filename_are_rejected(self):
        try:
            (self.output / "original.zip.part").symlink_to(self.source)
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(download.DownloadError):
            download.acquire_file({"name": "original.zip", "path": str(self.source)}, self.output, 0, 1)
        with self.assertRaises(download.DownloadError):
            download.acquire_file({"name": "acquisition.json", "path": str(self.source)}, self.output, 0, 1)
        self.assertEqual(self.source.read_bytes(), self.body)

    def test_interrupted_transfer_resumes_with_if_range(self):
        responses = [
            Response(self.body, headers={"ETag": '"v1"', "Content-Length": str(len(self.body))}, fail_after=20),
            Response(self.body[20:], 206, {"ETag": '"v1"',
                     "Content-Range": f"bytes 20-{len(self.body)-1}/{len(self.body)}"})
        ]
        with patch.object(download.OPENER, "open", side_effect=responses) as opened, patch.object(download.time, "sleep"):
            download.http_download("https://example.test/data", self.output / "data.zip", retries=1)
        request = opened.call_args_list[1].args[0]
        self.assertEqual(request.get_header("Range"), "bytes=20-")
        self.assertEqual(request.get_header("If-range"), '"v1"')
        self.assertEqual((self.output / "data.zip").read_bytes(), self.body)

    def test_server_ignoring_range_restarts_without_appending(self):
        responses = [
            Response(self.body, headers={"ETag": '"v1"', "Content-Length": str(len(self.body))}, fail_after=20),
            Response(self.body, headers={"ETag": '"v2"', "Content-Length": str(len(self.body))})
        ]
        with patch.object(download.OPENER, "open", side_effect=responses), patch.object(download.time, "sleep"):
            download.http_download("https://example.test/data", self.output / "data.zip", retries=1)
        self.assertEqual((self.output / "data.zip").read_bytes(), self.body)

    def test_wrong_resume_range_never_completes(self):
        responses = [
            Response(self.body, headers={"ETag": '"v1"', "Content-Length": str(len(self.body))}, fail_after=20),
            Response(self.body[20:], 206, {"ETag": '"v1"', "Content-Range": "bytes 0-20/21"})
        ]
        with patch.object(download.OPENER, "open", side_effect=responses), patch.object(download.time, "sleep"):
            with self.assertRaises(download.DownloadError):
                download.http_download("https://example.test/data", self.output / "data.zip", retries=1)
        self.assertFalse((self.output / "data.zip").exists())

    def test_416_restarts_instead_of_accepting_partial(self):
        url = "https://example.test/data"
        responses = [
            Response(self.body, headers={"ETag": '"v1"', "Content-Length": str(len(self.body))}, fail_after=20),
            HTTPError(url, 416, "range", {}, None),
            Response(self.body, headers={"Content-Length": str(len(self.body))})
        ]
        with patch.object(download.OPENER, "open", side_effect=responses) as opened, patch.object(download.time, "sleep"):
            download.http_download(url, self.output / "data.zip", retries=2)
        self.assertIsNone(opened.call_args_list[2].args[0].get_header("Range"))
        self.assertEqual((self.output / "data.zip").read_bytes(), self.body)

    def test_private_url_not_written_in_receipt_or_error(self):
        url = "https://example.test/SECRET-TOKEN/data.zip?signature=PRIVATE"
        with patch.object(download.OPENER, "open", return_value=Response(self.body)):
            download.acquire_file({"name": "data.zip", "url": url}, self.output, 0, 1)
        for path in self.output.glob("*.json"):
            text = path.read_text()
            self.assertNotIn("SECRET-TOKEN", text)
            self.assertNotIn("PRIVATE", text)
        with patch.object(download.OPENER, "open", side_effect=HTTPError(url, 403, url, {}, None)):
            with self.assertRaises(download.DownloadError) as raised:
                download.http_download(url, self.output / "other.zip", retries=0)
        self.assertNotIn("SECRET-TOKEN", str(raised.exception))

    def test_redirect_strips_auth_and_refuses_downgrade(self):
        request = Request("https://example.test/file", headers={"Authorization": "Bearer SECRET"})
        handler = download.SafeRedirect()
        redirected = handler.redirect_request(request, None, 302, "", {}, "https://cdn.test/file")
        self.assertIsNone(redirected.get_header("Authorization"))
        with self.assertRaises(download.DownloadError):
            handler.redirect_request(request, None, 302, "", {}, "http://cdn.test/file")

    def test_docile_base_and_full_selection(self):
        with patch.dict(download.os.environ, {"DOCILE_TOKEN": "not-a-real-token"}):
            base = download.docile_files(False)
            full = download.docile_files(True)
        self.assertEqual([f["name"] for f in base], ["annotated-trainval.zip", "test.zip"])
        self.assertEqual(len(full), 98)
        self.assertEqual(full[-1]["name"], "unlabeled-chunk-93.zip")
        self.assertEqual(full[2]["provenance"], "synthetic")

    def test_access_check_cannot_be_bypassed_by_local_files(self):
        dataset = {"needs_evidence": True, "provider": "manual"}
        self.assertEqual(download.prerequisites(dataset, {"files": [{}]}), "needs-access-evidence")
        self.assertEqual(download.prerequisites(dataset, {"files": [{}], "permission_evidence": "owner permission"}),
                         "ready-local-sources")

    def test_dataset_manifest_retry_and_configuration_change(self):
        dataset = {"id": "test", "provider": "manual", "source": "https://example.test",
                   "license": "fixture only", "provenance": "synthetic", "access": "configure input"}
        local = {"files": [{"name": "original.zip", "path": str(self.source)}]}
        result = download.acquire_dataset(dataset, local, self.output, self.args)
        self.assertEqual(result["status"], "acquired")
        result = download.acquire_dataset(dataset, local, self.output, self.args)
        self.assertEqual(result["status"], "verified-existing")
        with self.assertRaises(download.DownloadError):
            download.acquire_dataset(dataset, dict(local, permission_evidence="changed"), self.output, self.args)

    def test_lock_blocks_concurrent_acquisition(self):
        with download.dataset_lock(self.output):
            with self.assertRaises(download.DownloadError):
                with download.dataset_lock(self.output):
                    pass
        self.assertFalse((self.output / ".download.lock").exists())

    def test_dry_run_does_not_create_output_or_call_network(self):
        target = self.root / "untouched"
        with patch.object(download.OPENER, "open", side_effect=AssertionError("network")), redirect_stdout(io.StringIO()):
            code = download.main(["--dry-run", "--output", str(target), "--datasets", "hitl"])
        self.assertEqual(code, 0)
        self.assertFalse(target.exists())

    def test_missing_source_returns_nonzero_with_report(self):
        target = self.root / "run"
        with redirect_stdout(io.StringIO()):
            code = download.main(["--datasets", "hitl", "--output", str(target)])
        self.assertEqual(code, 1)
        reports = list(target.glob("download-report-*.json"))
        self.assertEqual(len(reports), 1)
        self.assertEqual(json.loads(reports[0].read_text())["datasets"][0]["status"], "needs-local-sources")


    def test_huggingface_adapter_pins_revision_and_hashes_files(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import Mock
        def snapshot(**kwargs):
            folder = Path(kwargs["local_dir"])
            (folder / "data.parquet").write_bytes(b"synthetic test payload")
            (folder / ".cache").mkdir()
            (folder / ".cache" / "metadata").write_text("not a dataset asset")
        client = Mock(side_effect=snapshot)
        with patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(snapshot_download=client)}):
            files = download.external_download(
                {"provider": "huggingface", "repo": "test/repo", "revision": "pinned"}, self.output)
        self.assertEqual(client.call_args.kwargs["revision"], "pinned")
        self.assertEqual(client.call_args.kwargs["repo_type"], "dataset")
        self.assertEqual([f["file"] for f in files], ["files/data.parquet"])

    def test_kaggle_adapter_uses_no_shell_or_embedded_credentials(self):
        from types import SimpleNamespace
        def run(command, **kwargs):
            self.assertNotIn("shell", kwargs)
            self.assertEqual(command[:5], ["kaggle", "datasets", "download", "-d", "test/repo"])
            (Path(command[-1]) / "dataset.zip").write_bytes(self.body)
            return SimpleNamespace(returncode=0)
        with patch.object(download.subprocess, "run", side_effect=run):
            files = download.external_download({"provider": "kaggle", "repo": "test/repo"}, self.output)
        self.assertEqual(files[0]["file"], "files/dataset.zip")

    def test_provider_failure_does_not_expose_secret_exception(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import Mock
        client = Mock(side_effect=RuntimeError("https://example.test/SECRET-TOKEN"))
        with patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(snapshot_download=client)}):
            with self.assertRaises(download.DownloadError) as raised:
                download.external_download({"provider": "huggingface", "repo": "test/repo", "revision": "pinned"},
                                           self.output)
        self.assertNotIn("SECRET-TOKEN", str(raised.exception))

    def test_all_selection_includes_local_gaps_and_reserve_is_opt_in(self):
        with redirect_stdout(io.StringIO()) as output:
            download.main(["--dry-run"])
        self.assertIn("vehicle-groups", output.getvalue())
        self.assertNotIn("carparts-seg", output.getvalue())
        with redirect_stdout(io.StringIO()) as output:
            download.main(["--dry-run", "--include-reserve"])
        self.assertIn("carparts-seg", output.getvalue())

    def test_failed_source_does_not_prevent_later_source(self):
        def acquire(dataset, local, directory, args):
            if dataset["id"] == "hitl":
                raise download.DownloadError("synthetic failure")
            return {"id": dataset["id"], "status": "acquired"}
        with patch.object(download, "acquire_dataset", side_effect=acquire) as mocked, redirect_stdout(io.StringIO()):
            code = download.main(["--datasets", "hitl", "cord", "--output", str(self.output)])
        self.assertEqual(code, 1)
        self.assertEqual(mocked.call_count, 2)

    def test_invalid_local_configuration_rejected_before_writes(self):
        from contextlib import redirect_stderr
        config = self.root / "invalid.json"
        config.write_text(json.dumps({"hitl": {"files": [{"name": "file", "url": "https://example.test",
                                                        "path": "also-local"}]}}))
        target = self.root / "must-not-exist"
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            download.main(["--sources", str(config), "--output", str(target)])
        self.assertEqual(raised.exception.code, 2)
        self.assertFalse(target.exists())



if __name__ == "__main__":
    unittest.main()
