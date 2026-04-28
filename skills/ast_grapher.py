import ast
import os
import json
from pathlib import Path

class ASTGrapher:
    def __init__(self, root_dir):
        self.root_dir = Path(root_dir)
        self.graph = {
            "files": {},
            "dependencies": []
        }

    def analyze_project(self):
        """Walks the directory and analyzes each Python file."""
        for path in self.root_dir.rglob("*.py"):
            if "venv" in str(path) or "__pycache__" in str(path):
                continue
            
            relative_path = str(path.relative_to(self.root_dir))
            self.graph["files"][relative_path] = self.analyze_file(path)

        return self.graph

    def analyze_file(self, file_path):
        """Parses a single file and extracts its structure."""
        with open(file_path, "r", encoding="utf-8") as f:
            try:
                tree = ast.parse(f.read())
            except SyntaxError:
                return {"error": "Syntax Error"}

        file_info = {
            "classes": [],
            "functions": [],
            "imports": []
        }

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                file_info["classes"].append({
                    "name": node.name,
                    "methods": [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))],
                    "lineno": node.lineno
                })
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                file_info["functions"].append({
                    "name": node.name,
                    "lineno": node.lineno
                })
        
        # Still walk for imports as they can be nested (though rare)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    file_info["imports"].append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                file_info["imports"].append(f"{node.module}.{node.names[0].name}" if node.module else node.names[0].name)

        return file_info

    def save_graph(self, output_path):
        """Saves the graph to a JSON file."""
        with open(output_path, "w") as f:
            json.dump(self.graph, f, indent=4)

if __name__ == "__main__":
    # Example: Analyze Faultline itself
    # grapher = ASTGrapher(root_dir=".")
    # grapher.analyze_project()
    # grapher.save_graph("project_map.json")
    pass
