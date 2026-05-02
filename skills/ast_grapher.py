import ast
import json
from pathlib import Path

SKIPPED_DIRS = {
    ".git", ".hg", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "__pycache__", ".aegis_patches", "venv", ".venv", "env", "node_modules",
    # build artifacts and vendored runtimes
    "dist", "build", "eggs", ".eggs", "htmlcov", "site-packages",
    # common embedded/vendored Python runtimes (e.g. WASM sandboxes)
    "sandbox", "vendor",
}


# ─────────────────────────────────────────────────────────────────────────────
# Call extractor — AST visitor that emits (caller, callee, call_info) triples
# ─────────────────────────────────────────────────────────────────────────────

class _CallExtractor(ast.NodeVisitor):
    """
    Visits one file's AST and emits call edges with argument-count information.
    Tracks class/function nesting so it knows the caller's node-ID.
    """

    def __init__(self, file_path: str, symbol_index: dict):
        self.file_path = file_path
        self.symbol_index = symbol_index   # name → [(node_id, file_path), ...]
        self.call_edges: list[dict] = []
        self._class_stack: list[str] = []
        self._func_stack: list[str] = []

    def visit_ClassDef(self, node):
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node):
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        caller_id = self._caller_id()
        if caller_id:
            name = self._resolve_name(node.func)
            if name:
                target_id = self._lookup(name)
                if target_id and target_id != caller_id:
                    # Capture argument shape for call-site validation
                    pos_args  = sum(1 for a in node.args if not isinstance(a, ast.Starred))
                    kw_names  = [kw.arg for kw in node.keywords if kw.arg is not None]
                    has_star  = any(isinstance(a, ast.Starred) for a in node.args)
                    has_dstar = any(kw.arg is None for kw in node.keywords)
                    self.call_edges.append({
                        "source": caller_id,
                        "target": target_id,
                        "call_info": {
                            "pos_args": pos_args,
                            "kw_names": kw_names,
                            "has_star":  has_star,
                            "has_dstar": has_dstar,
                            "lineno": node.lineno,
                            "file": self.file_path,
                        },
                    })
        self.generic_visit(node)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _caller_id(self) -> str | None:
        if not self._func_stack:
            return None
        if self._class_stack:
            return f"method:{self.file_path}:{self._class_stack[-1]}.{self._func_stack[-1]}"
        return f"func:{self.file_path}:{self._func_stack[-1]}"

    def _resolve_name(self, node) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = self._resolve_name(node.value)
            if parent == "self" and self._class_stack:
                return f"self.{node.attr}"
            return f"{parent}.{node.attr}" if parent else node.attr
        return None

    def _lookup(self, name: str) -> str | None:
        if name.startswith("self.") and self._class_stack:
            method = name[5:]
            tid = f"method:{self.file_path}:{self._class_stack[-1]}.{method}"
            if any(c[0] == tid for c in self.symbol_index.get(method, [])):
                return tid
            return None
        if "." in name:
            prefix, suffix = name.split(".", 1)
            for cid, cfp in self.symbol_index.get(prefix, []):
                if cid.startswith("class:"):
                    cls_name = cid.split(":")[-1]
                    tid = f"method:{cfp}:{cls_name}.{suffix}"
                    if any(c[0] == tid for c in self.symbol_index.get(suffix, [])):
                        return tid
            cands = self.symbol_index.get(suffix, [])
            return cands[0][0] if cands else None
        cands = self.symbol_index.get(name, [])
        if not cands:
            return None
        same = [c for c in cands if c[1] == self.file_path]
        return same[0][0] if same else cands[0][0]


# ─────────────────────────────────────────────────────────────────────────────
# Main grapher
# ─────────────────────────────────────────────────────────────────────────────

class ASTGrapher:
    def __init__(self, root_dir):
        self.root_dir = Path(root_dir)
        self.module_map: dict[str, str] = {}
        self._symbol_index: dict[str, list] = {}
        self.graph: dict = {}

    def analyze_project(self) -> dict:
        self.graph = {
            "files": {},
            "dependencies": [],
            "call_edges": [],
            "inheritance_edges": [],
            "signatures": {},       # node_id → signature dict
            "serializer_schemas": [],  # [{name, file, class_id, fields}, ...]
        }
        self._symbol_index = {}

        all_py = [
            p for p in self.root_dir.rglob("*.py")
            if not any(part in SKIPPED_DIRS for part in p.parts)
        ]

        # Build module → relative-path map
        for path in all_py:
            rel = path.relative_to(self.root_dir)
            mod = str(rel.with_suffix("")).replace("\\", ".").replace("/", ".")
            if mod.endswith(".__init__"):
                mod = mod[:-9]
            self.module_map[mod] = str(rel)

        # First pass: structure + symbol index
        for path in all_py:
            rel = str(path.relative_to(self.root_dir))
            info = self._analyze_file(path, rel)
            self.graph["files"][rel] = info
            self._index_symbols(rel, info)

            for imp in info.get("imports", []):
                tgt = self._resolve_import(imp)
                if tgt and tgt != rel and (rel, tgt) not in self.graph["dependencies"]:
                    self.graph["dependencies"].append((rel, tgt))

        # Second pass: call edges (with call_info), inheritance edges
        self._build_call_edges(all_py)
        self._build_inheritance_edges()

        return self.graph

    # ── symbol index ─────────────────────────────────────────────────────────

    def _index_symbols(self, fp: str, info: dict):
        for cls in info.get("classes", []):
            cid = f"class:{fp}:{cls['name']}"
            self._symbol_index.setdefault(cls["name"], []).append((cid, fp))
            for m in cls.get("methods", []):
                mid = f"method:{fp}:{cls['name']}.{m}"
                self._symbol_index.setdefault(m, []).append((mid, fp))
        for fn in info.get("functions", []):
            fid = f"func:{fp}:{fn['name']}"
            self._symbol_index.setdefault(fn["name"], []).append((fid, fp))

    # ── call edges ───────────────────────────────────────────────────────────

    def _build_call_edges(self, all_py: list):
        seen: set[tuple] = set()
        for path in all_py:
            rel = str(path.relative_to(self.root_dir))
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (SyntaxError, OSError):
                continue
            ext = _CallExtractor(rel, self._symbol_index)
            ext.visit(tree)
            for edge in ext.call_edges:
                key = (edge["source"], edge["target"])
                if key not in seen:
                    seen.add(key)
                    self.graph["call_edges"].append(edge)

    # ── inheritance edges ─────────────────────────────────────────────────────

    def _build_inheritance_edges(self):
        seen: set[tuple] = set()
        for fp, info in self.graph["files"].items():
            for cls in info.get("classes", []):
                cid = f"class:{fp}:{cls['name']}"
                for base in cls.get("bases", []):
                    if not base:
                        continue
                    simple = base.split(".")[-1]
                    for bid, _ in self._symbol_index.get(simple, []):
                        if bid.startswith("class:") and bid != cid:
                            key = (cid, bid)
                            if key not in seen:
                                seen.add(key)
                                self.graph["inheritance_edges"].append(
                                    {"source": cid, "target": bid}
                                )
                            break

    # ── file analysis ─────────────────────────────────────────────────────────

    def _analyze_file(self, path: Path, rel: str) -> dict:
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except SyntaxError:
            return {"error": "SyntaxError"}

        info: dict = {
            "classes": [], "functions": [], "imports": [],
            "django": {"routes": [], "serializers": [], "views": []},
        }

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                base_names = [self._name_from_node(b) for b in node.bases]
                methods = []
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.append(item.name)
                        mid = f"method:{rel}:{node.name}.{item.name}"
                        self.graph["signatures"][mid] = self._extract_signature(item)

                cls_entry = {
                    "name": node.name,
                    "bases": [b for b in base_names if b],
                    "methods": methods,
                    "lineno": node.lineno,
                }
                info["classes"].append(cls_entry)

                # Serializer schema extraction
                schema = self._extract_serializer_fields(node)
                if schema is not None:
                    cid = f"class:{rel}:{node.name}"
                    self.graph["serializer_schemas"].append({
                        "name": node.name,
                        "file": rel,
                        "class_id": cid,
                        "fields": schema,
                    })

                for base in base_names:
                    if not base:
                        continue
                    if "Serializer" in base:
                        info["django"]["serializers"].append(
                            {"name": node.name, "lineno": node.lineno, "bases": base_names}
                        )
                    if "View" in base or "ViewSet" in base:
                        info["django"]["views"].append(
                            {"name": node.name, "lineno": node.lineno, "bases": base_names}
                        )

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                info["functions"].append({"name": node.name, "lineno": node.lineno})
                fid = f"func:{rel}:{node.name}"
                self.graph["signatures"][fid] = self._extract_signature(node)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    info["imports"].append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    mod = node.module or ""
                    info["imports"].append(f"{mod}.{alias.name}" if mod else alias.name)
            elif isinstance(node, ast.Call):
                route = self._route_from_call(node)
                if route:
                    info["django"]["routes"].append(route)

        return info

    # ── signature extraction ──────────────────────────────────────────────────

    def _extract_signature(self, node) -> dict:
        """Extract a compact signature dict from a FunctionDef/AsyncFunctionDef."""
        args = node.args
        params: list[dict] = []

        regular = args.posonlyargs + args.args
        n_reg = len(regular)
        n_def = len(args.defaults)

        for i, arg in enumerate(regular):
            if arg.arg in ("self", "cls"):
                continue
            has_default = i >= (n_reg - n_def)
            params.append({
                "name": arg.arg,
                "annotation": self._annotation_str(arg.annotation),
                "required": not has_default,
                "kwonly": False,
            })

        for i, arg in enumerate(args.kwonlyargs):
            has_default = args.kw_defaults[i] is not None
            params.append({
                "name": arg.arg,
                "annotation": self._annotation_str(arg.annotation),
                "required": not has_default,
                "kwonly": True,
            })

        regular_params = [p for p in params if not p["kwonly"]]
        min_pos = sum(1 for p in regular_params if p["required"])
        max_pos = len(regular_params)

        # Build compact human-readable signature string for UI
        parts = []
        for p in params:
            s = p["name"]
            if p["annotation"]:
                s += f": {p['annotation']}"
            if not p["required"]:
                s += " = ..."
            parts.append(s)
        ret = self._annotation_str(node.returns)
        sig_str = f"({', '.join(parts)})"
        if ret:
            sig_str += f" → {ret}"

        return {
            "params": params,
            "min_positional": min_pos,
            "max_positional": max_pos,
            "has_var_positional": args.vararg is not None,
            "has_var_keyword": args.kwarg is not None,
            "return_annotation": ret,
            "sig_str": sig_str,
        }

    def _annotation_str(self, node) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            p = self._annotation_str(node.value)
            return f"{p}.{node.attr}" if p else node.attr
        if isinstance(node, ast.Subscript):
            v = self._annotation_str(node.value)
            s = self._annotation_str(node.slice)
            return f"{v}[{s}]" if v and s else v
        if isinstance(node, ast.Constant):
            return repr(node.value)
        if isinstance(node, ast.Tuple):
            return ", ".join(filter(None, (self._annotation_str(e) for e in node.elts)))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            l = self._annotation_str(node.left)
            r = self._annotation_str(node.right)
            return f"{l} | {r}" if l and r else (l or r)
        return None

    # ── serializer schema extraction ──────────────────────────────────────────

    def _extract_serializer_fields(self, class_node: ast.ClassDef) -> list | None:
        """
        If the class looks like a DRF Serializer, extract each field declaration.
        Returns a list of field dicts or None if the class is not a serializer.
        """
        base_names = [self._name_from_node(b) for b in class_node.bases]
        is_serializer = (
            any(b and "Serializer" in b for b in base_names)
            or class_node.name.endswith("Serializer")
        )
        if not is_serializer:
            return None

        fields: list[dict] = []
        for stmt in class_node.body:
            if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, ast.Call):
                continue
            for target in stmt.targets:
                if not isinstance(target, ast.Name) or target.id.startswith("_"):
                    continue

                raw_type = self._name_from_node(stmt.value.func)
                if not raw_type:
                    continue
                field_type = raw_type.split(".")[-1]
                if not (field_type.endswith("Field") or field_type.endswith("Serializer")):
                    continue

                required = True
                kwargs: dict = {}
                for kw in stmt.value.keywords:
                    if kw.arg is None:
                        continue
                    val = None
                    if isinstance(kw.value, ast.Constant):
                        val = kw.value.value
                    elif isinstance(kw.value, ast.Name):
                        val = kw.value.id
                    if val is not None:
                        if kw.arg == "required":
                            required = val not in (False, "False", "None", None)
                        else:
                            kwargs[kw.arg] = val

                fields.append({
                    "name": target.id,
                    "type": field_type,
                    "required": required,
                    "kwargs": kwargs,
                })

        return fields if fields else None

    # ── shared utils ──────────────────────────────────────────────────────────

    def _resolve_import(self, name: str) -> str | None:
        parts = name.split(".")
        while parts:
            if ".".join(parts) in self.module_map:
                return self.module_map[".".join(parts)]
            parts.pop()
        return None

    def _name_from_node(self, node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            p = self._name_from_node(node.value)
            return f"{p}.{node.attr}" if p else node.attr
        if isinstance(node, ast.Call):
            return self._name_from_node(node.func)
        return None

    def _literal_arg(self, node):
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

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
        with open(output_path, "w") as f:
            json.dump(self.graph, f, indent=4)
