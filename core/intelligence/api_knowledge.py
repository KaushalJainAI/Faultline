import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional
from langchain_core.tools import tool

logger = logging.getLogger("AegisAgent")

@tool
def query_api_knowledge(endpoint: str, run_folder: str) -> str:
    """
    Query detailed API schema or metadata for a specific endpoint.
    Use this to understand request body structures, parameters, and authentication requirements.
    
    Args:
        endpoint: The exact endpoint path (e.g., '/api/auth/login/')
        run_folder: The current campaign run folder (passed automatically by the agent)
    """
    try:
        schema_path = Path(run_folder) / "api_schemas.json"
        if not schema_path.exists():
            return f"Error: api_schemas.json not found in {run_folder}. Discovery phase may have failed."
            
        with open(schema_path, "r", encoding="utf-8") as f:
            schemas = json.load(f)
            
        # Normalize endpoint
        endpoint = endpoint.strip().lower()
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
            
        # Try exact match
        if endpoint in schemas:
            return json.dumps(schemas[endpoint], indent=2)
            
        # Try matching without trailing slash or prefix
        for k, v in schemas.items():
            if k.strip().lower().rstrip("/") == endpoint.rstrip("/"):
                return json.dumps(v, indent=2)
                
        return f"Endpoint '{endpoint}' not found in API schemas. Use list_run_folder_files to check for endpoint_map.json."
        
    except Exception as e:
        logger.error(f"Error querying API knowledge: {e}")
        return f"Error querying API knowledge: {str(e)}"

