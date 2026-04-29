import sys

file_path = 'core/agent.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace CampaignState
target_state = """class CampaignState(TypedDict):
    \"\"\"
    Standard LangGraph State containing messages and campaign context.
    add_messages ensures we append to the list rather than overwrite.
    \"\"\"
    messages: Annotated[list[BaseMessage], add_messages]
    target_dir: str
    target_url: str
    log_file: str"""

replacement_state = """class CampaignState(TypedDict):
    \"\"\"
    Standard LangGraph State containing messages and campaign context.
    add_messages ensures we append to the list rather than overwrite.
    \"\"\"
    messages: Annotated[list[BaseMessage], add_messages]
    target_dir: str
    target_url: str
    log_file: str
    session_headers: dict"""

content = content.replace(target_state, replacement_state)

# Replace run_campaign
target_run = """    async def run_campaign(self, target_dir: str, target_url: str, log_file: str, initial_prompt: str = "Begin the chaos campaign against the target."):
        \"\"\"
        Entry point to start the campaign stream.
        \"\"\"
        initial_state = {
            "messages": [HumanMessage(content=initial_prompt)],
            "target_dir": target_dir,
            "target_url": target_url,
            "log_file": log_file,
        }"""

replacement_run = """    async def run_campaign(self, target_dir: str, target_url: str, log_file: str, session_headers: dict = None, initial_prompt: str = "Begin the chaos campaign against the target."):
        \"\"\"
        Entry point to start the campaign stream.
        \"\"\"
        initial_state = {
            "messages": [HumanMessage(content=initial_prompt)],
            "target_dir": target_dir,
            "target_url": target_url,
            "log_file": log_file,
            "session_headers": session_headers or {},
        }"""

content = content.replace(target_run, replacement_run)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print('File patched successfully.')
