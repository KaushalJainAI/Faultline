import tempfile
from unittest.mock import patch, MagicMock

from django.test import TestCase

from core.orchestration.pipeline import PipelineRunner

class PipelineRunnerTests(TestCase):
    @patch("core.orchestration.pipeline.DeterministicChecker")
    @patch("core.orchestration.pipeline.analyze_project_structure")
    def test_run_pipeline_orchestrates_correctly(self, mock_analyze, mock_checker_cls):
        mock_checker = MagicMock()
        mock_checker.run_all.return_value = {
            "summary": {"total_findings": 0, "high_or_critical": 0},
            "findings": [],
            "dependency_root_causes": []
        }
        mock_checker_cls.return_value = mock_checker
        mock_analyze.invoke.return_value = '{"files": {"a.py": {}}, "dependencies": []}'

        with tempfile.TemporaryDirectory() as target_dir:
            runner = PipelineRunner(target_dir=target_dir)
            
            # Run without semantic indexing for simplicity
            report = runner.run(include_semantic=False)
            
            self.assertEqual(report["mode"], "pipeline-first")
            self.assertEqual(report["stages"]["dependency_graph"]["files"], 1)
            self.assertIn("report_path", report)

    @patch("core.orchestration.pipeline.DeterministicChecker")
    @patch("core.orchestration.pipeline.analyze_project_structure")
    def test_run_pipeline_handles_analyze_exception(self, mock_analyze, mock_checker_cls):
        mock_checker = MagicMock()
        mock_checker.run_all.return_value = {
            "summary": {"total_findings": 0, "high_or_critical": 0},
            "findings": [],
            "dependency_root_causes": []
        }
        mock_checker_cls.return_value = mock_checker
        mock_analyze.invoke.side_effect = Exception("Analysis failed")

        with tempfile.TemporaryDirectory() as target_dir:
            runner = PipelineRunner(target_dir=target_dir)
            
            # Should not crash but instead return default/empty structure
            try:
                report = runner.run(include_semantic=False)
            except Exception:
                self.fail("Pipeline crashed on tool exception")
                
            self.assertIn("report_path", report)
