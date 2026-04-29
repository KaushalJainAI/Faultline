import json
import sys
import os

# Add the current directory to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.tools import analyze_project_structure

def run():
    target_dir = r"C:\Users\91700\Desktop\AIAAS\Backend"
    print(f"Mapping project at: {target_dir}")
    
    # The analyze_project_structure is a LangChain @tool, we can call it using .invoke()
    try:
        # Pass the dictionary argument if it expects kwargs, or string if args
        result = analyze_project_structure.invoke({"target_dir": target_dir})
        
        # Save the result to a file so we can inspect it
        output_path = r"C:\Users\91700\Desktop\Faultline\reports\aiaas_map.json"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result)
            
        print(f"Successfully mapped project. Output saved to: {output_path}")
        
    except Exception as e:
        print(f"Error mapping project: {e}")

if __name__ == "__main__":
    run()
