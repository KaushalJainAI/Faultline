import json
import sys

try:
    with open(r"C:\Users\91700\Desktop\Faultline\reports\aiaas_map.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        
    views = []
    
    for file_path, file_data in data.items():
        if "views.py" in file_path or "api" in file_path:
            for cls_name, cls_data in file_data.get("classes", {}).items():
                # check decorators or inheritances
                bases = cls_data.get("bases", [])
                decorators = cls_data.get("decorators", [])
                if any("View" in b for b in bases) or any("ViewSet" in b for b in bases) or any("API" in b for b in bases):
                    views.append((file_path, cls_name, decorators))
            for func_name, func_data in file_data.get("functions", {}).items():
                decorators = func_data.get("decorators", [])
                if any("api_view" in d for d in decorators) or any("action" in d for d in decorators):
                    views.append((file_path, func_name, decorators))
                    
    print(f"Found {len(views)} views/endpoints.")
    for v in views[:30]:
        print(f"File: {v[0]}\n  -> {v[1]} (Decorators: {v[2]})")
except Exception as e:
    print(f"Error parsing map: {e}")
