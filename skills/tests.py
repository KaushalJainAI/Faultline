import tempfile
from pathlib import Path
from django.test import TestCase
from unittest.mock import patch, MagicMock

from skills.deterministic_checker import DeterministicChecker
from skills.medic import Medic

class DeterministicCheckerTests(TestCase):
    def test_run_all_detects_syntax_errors(self):
        with tempfile.TemporaryDirectory() as target_dir:
            root = Path(target_dir)
            # Create a file with bad syntax
            (root / "bad_syntax.py").write_text("def missing_colon()\n    pass\n", encoding="utf-8")
            
            checker = DeterministicChecker(str(root))
            result = checker.run_all()
            
            self.assertTrue(result["summary"]["total_findings"] > 0)
            finding = result["findings"][0]
            self.assertEqual(finding["category"], "syntax")
            self.assertEqual(finding["file_path"], "bad_syntax.py")
            self.assertEqual(finding["severity"], "critical")

class MedicTests(TestCase):
    @patch("skills.medic.psutil.Process")
    @patch("skills.medic.subprocess.Popen")
    @patch("skills.medic.httpx.Client")
    def test_medic_starts_and_stops_server(self, mock_httpx_cls, mock_popen, mock_process_cls):
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.pid = 12345
        mock_popen.return_value = mock_process
        
        mock_psutil_proc = MagicMock()
        mock_psutil_proc.children.return_value = []
        mock_process_cls.return_value = mock_psutil_proc
        
        mock_client = MagicMock()
        mock_httpx_cls.return_value.__enter__.return_value = mock_client
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.get.return_value = mock_response

        medic = Medic(target_dir=".", start_command="python -m http.server", health_url="http://localhost:8000")
        
        started = medic.start_server()
        self.assertTrue(started)
        mock_popen.assert_called_once()
        mock_client.get.assert_called()
        
        medic.kill_server()
        mock_process.wait.assert_called_once()

    @patch("skills.medic.subprocess.Popen")
    def test_medic_fails_to_start_if_process_dies(self, mock_popen):
        mock_process = MagicMock()
        mock_process.poll.return_value = 1  # Process died
        mock_process.stderr.read.return_value = b"SyntaxError"
        mock_popen.return_value = mock_process
        
        medic = Medic(target_dir=".", start_command="python bad.py", health_url=None)
        
        started = medic.start_server()
        self.assertFalse(started)

    @patch("skills.medic.subprocess.Popen")
    @patch("skills.medic.httpx.Client")
    def test_medic_health_check_timeout(self, mock_httpx_cls, mock_popen):
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process
        
        mock_client = MagicMock()
        mock_httpx_cls.return_value.__enter__.return_value = mock_client
        import httpx
        mock_client.get.side_effect = httpx.RequestError("Timeout")

        # Use a short timeout by mocking time or by letting it fail if hardcoded.
        # Actually, medic uses a 30s timeout. Let's mock time.monotonic to simulate timeout instantly.
        with patch("skills.medic.time.monotonic", side_effect=[0, 31]):
            medic = Medic(target_dir=".", start_command="python -m http.server", health_url="http://localhost:8000")
            started = medic.start_server()
            self.assertFalse(started)

class ASTGrapherBadPathTests(TestCase):
    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as target_dir:
            checker = DeterministicChecker(target_dir)
            result = checker.run_all()
            self.assertEqual(result["summary"]["total_findings"], 0)
            self.assertEqual(result["findings"], [])
