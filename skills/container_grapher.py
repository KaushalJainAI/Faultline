import json
from pathlib import Path
from typing import Dict, List, Set, Any

class ContainerGrapher:
    """
    Groups file-level dependencies into a high-level "Container" architecture.
    Calculates modularity metrics to assess independence and health.
    """

    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir).resolve()
        self.containers: Dict[str, Dict] = {}
        self.edges: List[Dict] = [] # container-to-container edges

    def analyze_modularity(self, ast_graph: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main entry point. Takes the file-level AST graph and returns 
        a high-level modularity assessment.
        """
        # 1. Group files into containers
        self._build_containers(ast_graph)
        
        # 2. Map dependencies to containers
        self._map_dependencies(ast_graph)
        
        # 3. Map call edges to containers and identify public surfaces
        self._map_calls(ast_graph)

        # 4. Calculate metrics
        self._calculate_metrics()

        return {
            "containers": self.containers,
            "edges": self.edges,
            "mermaid": self.generate_mermaid(),
        }

    def _build_containers(self, ast_graph: Dict[str, Any]):
        """Groups files by their top-level directory."""
        for rel_path in ast_graph.get("files", {}):
            parts = Path(rel_path).parts
            # If in root, put in 'root' container; otherwise use top directory
            container_id = parts[0] if len(parts) > 1 else "root"
            if container_id.endswith(".py"): # File in root
                container_id = "root"

            if container_id not in self.containers:
                self.containers[container_id] = {
                    "id": container_id,
                    "files": [],
                    "internal_deps": 0,
                    "external_deps_out": 0,
                    "external_deps_in": 0,
                    "public_surface": set(),
                    "metrics": {},
                    "status": "unknown"
                }
            self.containers[container_id]["files"].append(rel_path)

    def _map_dependencies(self, ast_graph: Dict[str, Any]):
        """Aggregates import-level dependencies."""
        seen_edges = set()
        for source_file, target_file in ast_graph.get("dependencies", []):
            src_container = self._get_container_of(source_file)
            tgt_container = self._get_container_of(target_file)

            if src_container == tgt_container:
                self.containers[src_container]["internal_deps"] += 1
            else:
                self.containers[src_container]["external_deps_out"] += 1
                self.containers[tgt_container]["external_deps_in"] += 1
                
                edge_key = (src_container, tgt_container)
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    self.edges.append({
                        "source": src_container,
                        "target": tgt_container,
                        "type": "import"
                    })

    def _map_calls(self, ast_graph: Dict[str, Any]):
        """Aggregates function/class calls and identifies the public surface."""
        seen_edges = set()
        for edge in ast_graph.get("call_edges", []):
            # source/target IDs are usually: "func:file_path:name"
            src_id = edge["source"]
            tgt_id = edge["target"]

            src_file = src_id.split(":")[1] if ":" in src_id else None
            tgt_file = tgt_id.split(":")[1] if ":" in tgt_id else None

            if not src_file or not tgt_file:
                continue

            src_container = self._get_container_of(src_file)
            tgt_container = self._get_container_of(tgt_file)

            if src_container == tgt_container:
                self.containers[src_container]["internal_deps"] += 1
            else:
                self.containers[src_container]["external_deps_out"] += 1
                self.containers[tgt_container]["external_deps_in"] += 1
                
                # The target symbol is part of the public surface of tgt_container
                symbol_name = tgt_id.split(":")[-1]
                self.containers[tgt_container]["public_surface"].add(symbol_name)

                edge_key = (src_container, tgt_container)
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    self.edges.append({
                        "source": src_container,
                        "target": tgt_container,
                        "type": "call"
                    })

    def _calculate_metrics(self):
        """Calculates Independence and Cohesion scores."""
        for cid, data in self.containers.items():
            internal = data["internal_deps"]
            ext_out = data["external_deps_out"]
            ext_in = data["external_deps_in"]
            
            total_edges = internal + ext_out + ext_in
            
            # Independence Score: ratio of internal work to total interactions
            # If total_edges is 0, we can't score it (isolated module)
            if total_edges > 0:
                independence = (internal / (internal + ext_out)) * 100
                instability = ext_out / (ext_out + ext_in) if (ext_out + ext_in) > 0 else 0
            else:
                independence = 100.0
                instability = 0.0

            # Cohesion: how many internal links per file
            file_count = len(data["files"])
            cohesion = internal / file_count if file_count > 0 else 0

            data["metrics"] = {
                "independence_score": round(independence, 1),
                "instability": round(instability, 2),
                "cohesion_density": round(cohesion, 2),
                "public_api_size": len(data["public_surface"])
            }

            # Final Status Assessment
            if independence > 80:
                data["status"] = "🟢 Correct (Independent)"
            elif independence > 50:
                data["status"] = "🟡 Fair (Coupled)"
            else:
                data["status"] = "🔴 Wrong (Entangled)"

    def _get_container_of(self, file_path: str) -> str:
        parts = Path(file_path).parts
        if not parts:
            return "root"
        container_id = parts[0]
        if container_id.endswith(".py") or len(parts) == 1:
            return "root"
        return container_id

    def generate_mermaid(self) -> str:
        """Produces a high-level container relationship diagram."""
        lines = ["graph TD", "    subgraph project [Project Architecture]"]
        
        # Add nodes
        for cid, data in self.containers.items():
            status_icon = "🟢" if "Correct" in data["status"] else ("🔴" if "Wrong" in data["status"] else "🟡")
            label = f"{status_icon} {cid}<br/>Score: {data['metrics']['independence_score']}"
            lines.append(f'        {cid}["{label}"]')

        # Add edges
        for edge in self.edges:
            src = edge["source"]
            tgt = edge["target"]
            # Thicker lines for import+call (tighter coupling)
            line = "==>" if edge["type"] == "call" else "-->"
            lines.append(f"        {src} {line} {tgt}")

        lines.append("    end")
        
        # Apply styles
        lines.append("    classDef correct stroke:#2ecc71,stroke-width:2px;")
        lines.append("    classDef fair stroke:#f1c40f,stroke-width:2px;")
        lines.append("    classDef wrong stroke:#e74c3c,stroke-width:2px;")
        
        for cid, data in self.containers.items():
            if "Correct" in data["status"]:
                lines.append(f"    class {cid} correct")
            elif "Wrong" in data["status"]:
                lines.append(f"    class {cid} wrong")
            else:
                lines.append(f"    class {cid} fair")

        return "\n".join(lines)

    def save_assessment(self, output_path: str):
        # Convert set to list for JSON serialization
        results = {
            "containers": {
                cid: {**data, "public_surface": sorted(list(data["public_surface"]))}
                for cid, data in self.containers.items()
            },
            "edges": self.edges,
            "mermaid": self.generate_mermaid()
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=4)
