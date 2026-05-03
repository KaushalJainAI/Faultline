import re

output = """
C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python311\\Lib\\site-packages\\pytest_asyncio\\plugin.py:247: PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset.
  C:\\Users\\91700\\Desktop\\Faultline\\scratch\\test_deprecation\\main.py:4: DeprecationWarning: old_func is deprecated
"""

# Try different regexes
regexes = [
    r"^\s*(.*?):(\d+): (\w*Warning): (.*)",
    r"^\s*(.+):(\d+): (\w*Warning): (.*)",
    r"^\s*((?:[a-zA-Z]:)?[^:]+):(\d+): (\w*Warning): (.*)"
]

for r in regexes:
    print(f"\nTesting: {r}")
    pattern = re.compile(r, re.MULTILINE)
    matches = list(pattern.finditer(output))
    print(f"Found {len(matches)} matches")
    for m in matches:
        print(f" - Group 1 (path): {m.group(1)}")
        print(f" - Group 2 (line): {m.group(2)}")
        print(f" - Group 3 (cat):  {m.group(3)}")
        print(f" - Group 4 (msg):  {m.group(4)}")
