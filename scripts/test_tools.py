#!/usr/bin/env python3
import asyncio
import os
import sys

# Add the parent directory to sys.path so we can import 'app'
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.tools import analyze_project_structure, validate_python_code, execute_chaos_campaign, save_vulnerability_report

async def run_tool_tests():
    print("🧪 Faultline Tool Testing Suite\n" + "="*40)
    
    # Test 1: Cartographer Tool
    print("\n[1] Testing: analyze_project_structure")
    target_dir = "."
    print(f"Targeting: {target_dir}")
    try:
        # Note: Tool is a function wrapped by Langchain. We invoke it.
        result = analyze_project_structure.invoke(target_dir)
        print(f"✅ Success! Generated JSON map with length: {len(result)} chars")
    except Exception as e:
        print(f"❌ Failed: {e}")

    # Test 2: Guardrail Tool
    print("\n[2] Testing: validate_python_code")
    code = "import json\nprint(json.dumps({'key': 'value'}))"
    try:
        result = validate_python_code.invoke({"code_string": code, "target_dir": "."})
        print(f"✅ Code snippet status: {result}")
    except Exception as e:
        print(f"❌ Failed: {e}")

    # Test 3: Save Report Tool
    print("\n[3] Testing: save_vulnerability_report")
    mock_report = "# Mock Report\nThis is a test of the save report tool."
    try:
        result = save_vulnerability_report.invoke({"report_markdown": mock_report, "filename": "mock_report.md"})
        print(f"✅ Result: {result}")
    except Exception as e:
        print(f"❌ Failed: {e}")

    print("\n🎉 Core Tool Tests Completed.")
    print("Note: execute_chaos_campaign was skipped as it requires a live target URL.")

if __name__ == "__main__":
    asyncio.run(run_tool_tests())
