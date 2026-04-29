import sys

file_path = 'core/agent.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

target = """        # Add dynamic system context
        context_msg = SystemMessage(
            content=f"{SYSTEM_PROMPT}\\n\\nTarget Config:\\n- Directory: {state.get('target_dir')}\\n- URL: {state.get('target_url')}\\n- Log File: {state.get('log_file')}\\n\\nYou must aggressively investigate the structure, validate attacks, and fire them."
        )"""

replacement = """        # Add dynamic system context
        session_headers = state.get('session_headers', {})
        header_str = "\\n- Session Headers: " + str(session_headers) if session_headers else ""
        
        context_msg = SystemMessage(
            content=f"{SYSTEM_PROMPT}\\n\\nTarget Config:\\n- Directory: {state.get('target_dir')}\\n- URL: {state.get('target_url')}\\n- Log File: {state.get('log_file')}{header_str}\\n\\nYou must aggressively investigate the structure, validate attacks, and fire them. If writing functional tests, include the Session Headers in your requests to bypass authentication."
        )"""

content = content.replace(target, replacement)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print('File patched successfully.')
