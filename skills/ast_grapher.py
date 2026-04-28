import ast
import json
from pathlib import Path

SKIPPED_DIRS = {".git", ".hg", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__", ".aegis_patches", "venv", ".venv", "env"}

class ASTGrapher:
    def __init__(self, root_dir):
        self.root_dir = Path(root_dir)
        self.graph = {
            "files": {},
            "dependencies": []
        }
        self.module_map = {}

    def analyze_project(self):
        """Walks the directory and analyzes each Python file."""
        self.graph = {"files": {}, "dependencies": []}
        all_py_files = list(self.root_dir.rglob("*.py"))
        
        # Mapping of module names to file paths for internal resolution
        self.module_map = {}
        for path in all_py_files:
            if any(part in SKIPPED_DIRS for part in path.parts):
                continue
            rel_path = path.relative_to(self.root_dir)
            module_name = str(rel_path.with_suffix("")).replace("\\", ".").replace("/", ".")
            if module_name.endswith(".__init__"):
                module_name = module_name[:-9]
            self.module_map[module_name] = str(rel_path)

        for path in all_py_files:
            if any(part in SKIPPED_DIRS for part in path.parts):
                continue
            
            relative_path = str(path.relative_to(self.root_dir))
            file_info = self.analyze_file(path)
            self.graph["files"][relative_path] = file_info
            
            # Resolve imports to internal dependencies
            for imp in file_info["imports"]:
                target_file = self._resolve_internal_import(imp)
                if target_file and target_file != relative_path:
                    edge = (relative_path, target_file)
                    if edge not in self.graph["dependencies"]:
                        self.graph["dependencies"].append(edge)

        return self.graph

    def _resolve_internal_import(self, import_name):
        """Attempts to resolve an import string to a local project file."""
        parts = import_name.split(".")
        while parts:
            module = ".".join(parts)
            if module in self.module_map:
                return self.module_map[module]
            parts.pop()
        return None

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
            "imports": [],
            "django": {
                "routes": [],
                "serializers": [],
                "views": []
            }
        }

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                base_names = [self._name_from_node(base) for base in node.bases]
                file_info["classes"].append({
                    "name": node.name,
                    "methods": [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))],
                    "lineno": node.lineno
                })
                if any(base and ("Serializer" in base or "View" in base or "ViewSet" in base) for base in base_names):
                    if any(base and "Serializer" in base for base in base_names):
                        file_info["django"]["serializers"].append({"name": node.name, "lineno": node.lineno, "bases": base_names})
                    if any(base and ("View" in base or "ViewSet" in base) for base in base_names):
                        file_info["django"]["views"].append({"name": node.name, "lineno": node.lineno, "bases": base_names})
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
                for alias in node.names:
                    file_info["imports"].append(f"{node.module}.{alias.name}" if node.module else alias.name)
            elif isinstance(node, ast.Call):
                route = self._route_from_call(node)
                if route:
                    file_info["django"]["routes"].append(route)

        return file_info

    def _name_from_node(self, node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = self._name_from_node(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        if isinstance(node, ast.Call):
            return self._name_from_node(node.func)
        return None

    def _literal_arg(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def _route_from_call(self, node):
        func_name = self._name_from_node(node.func)
        if func_name in {"path", "re_path", "url"} and node.args:
            return {
                "type": func_name,
                "route": self._literal_arg(node.args[0]) or "",
                "view": self._name_from_node(node.args[1]) if len(node.args) > 1 else "",
                "lineno": node.lineno,
            }
        if func_name and func_name.endswith(".register") and node.args:
            return {
                "type": "router.register",
                "route": self._literal_arg(node.args[0]) or "",
                "view": self._name_from_node(node.args[1]) if len(node.args) > 1 else "",
                "lineno": node.lineno,
            }
        return None

    def save_graph(self, output_path):
        """Saves the graph to a JSON file."""
        with open(output_path, "w") as f:
            json.dump(self.graph, f, indent=4)

if __name__ == "__main__":
    pass
