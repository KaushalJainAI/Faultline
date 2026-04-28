import os
import json
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
from typing import List, Dict, Any

class Visualizer:
    def __init__(self, reports_dir: str = "reports"):
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate_mermaid_dependency_graph(self, ast_graph: Dict[str, Any], output_filename: str = "dependency_graph.md"):
        """Generates a Mermaid.js dependency graph from AST data."""
        lines = ["```mermaid", "graph TD"]
        
        # Add nodes (files)
        for rel_path in ast_graph.get("files", {}):
            node_id = rel_path.replace("\\", "_").replace("/", "_").replace(".", "_")
            lines.append(f'    {node_id}["{rel_path}"]')
            
        # Add edges (dependencies)
        for source, target in ast_graph.get("dependencies", []):
            s_id = source.replace("\\", "_").replace("/", "_").replace(".", "_")
            t_id = target.replace("\\", "_").replace("/", "_").replace(".", "_")
            lines.append(f"    {s_id} --> {t_id}")
            
        lines.append("```")
        
        output_path = self.reports_dir / output_filename
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return str(output_path)

    def generate_campaign_charts(self, campaign_id: str, tool_runs: List[Dict], findings: List[Dict]):
        """Generates failure rate and vulnerability charts using Plotly."""
        # 1. Failure Rate Chart
        df_tools = pd.DataFrame(tool_runs)
        if not df_tools.empty:
            fig_fail = px.pie(df_tools, names='status', title=f"Campaign {campaign_id} Tool Execution Status",
                             color='status', color_discrete_map={'passed':'green', 'failed':'red', 'error':'orange', 'running':'blue'})
            fail_path = self.reports_dir / f"campaign_{campaign_id}_failure_rate.html"
            fig_fail.write_html(str(fail_path))
        else:
            fail_path = None

        # 2. Vulnerability by Endpoint
        df_findings = pd.DataFrame(findings)
        if not df_findings.empty:
            # Group by file_path or endpoint (if available in summary)
            endpoint_stats = df_findings.groupby('file_path').size().reset_index(name='finding_count')
            fig_vuln = px.bar(endpoint_stats, x='file_path', y='finding_count', title="Findings per Component",
                             labels={'file_path': 'Component/File', 'finding_count': 'Number of Findings'})
            vuln_path = self.reports_dir / f"campaign_{campaign_id}_vulnerability_map.html"
            fig_vuln.write_html(str(vuln_path))
        else:
            vuln_path = None

        return {
            "failure_rate_chart": str(fail_path) if fail_path else None,
            "vulnerability_map": str(vuln_path) if vuln_path else None
        }

    def calculate_scores(self, findings: List[Dict], functional_tests_passed: int, total_functional_tests: int) -> Dict[str, Any]:
        """Calculates vulnerability degree and global quality score."""
        severity_weights = {
            "critical": 10,
            "high": 5,
            "medium": 2,
            "low": 1
        }
        
        total_risk_score = 0
        endpoint_scores = {}
        
        for f in findings:
            weight = severity_weights.get(f.get("severity", "medium").lower(), 2)
            total_risk_score += weight
            
            fp = f.get("file_path", "unknown")
            endpoint_scores[fp] = endpoint_scores.get(fp, 0) + weight

        # Global Quality Score (0-100)
        # Starts at 100, drops based on findings and test failures
        test_penalty = 0
        if total_functional_tests > 0:
            test_penalty = (1 - (functional_tests_passed / total_functional_tests)) * 40
            
        finding_penalty = min(total_risk_score * 2, 60) # Cap finding penalty at 60
        
        quality_score = max(0, 100 - test_penalty - finding_penalty)
        
        return {
            "global_quality_score": round(quality_score, 2),
            "total_risk_score": total_risk_score,
            "endpoint_risk_scores": endpoint_scores
        }

    def generate_intent_correlation(self, intent_docs: List[Dict], implementation_map: Dict[str, Any]):
        """
        Visualizes correlation between documentation (intent) and code (implementation).
        Currently a placeholder showing coverage of docs vs files.
        """
        doc_files = {d.get("meta", {}).get("path") for d in intent_docs}
        impl_files = set(implementation_map.get("files", {}).keys())
        
        correlation = {
            "documented_files": list(doc_files.intersection(impl_files)),
            "undocumented_files": list(impl_files - doc_files),
            "stale_docs": list(doc_files - impl_files)
        }
        
        # Create a simple plotly indicator
        coverage = len(correlation["documented_files"]) / len(impl_files) if impl_files else 0
        fig = go.Figure(go.Indicator(
            mode = "gauge+number",
            value = coverage * 100,
            title = {'text': "Intent-Implementation Alignment (%)"},
            gauge = {'axis': {'range': [None, 100]},
                     'bar': {'color': "darkblue"},
                     'steps' : [
                         {'range': [0, 50], 'color': "gray"},
                         {'range': [50, 80], 'color': "lightgray"},
                         {'range': [80, 100], 'color': "lightblue"}]}))
        
        corr_path = self.reports_dir / "intent_correlation.html"
        fig.write_html(str(corr_path))
        
        return {
            "alignment_score": round(coverage * 100, 2),
            "chart": str(corr_path),
            "details": correlation
        }
