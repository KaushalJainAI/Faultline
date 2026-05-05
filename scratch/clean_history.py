import json
import os
from pathlib import Path

def clean_checkpoint(path: str):
    print(f"Opening {path}...")
    if not os.path.exists(path):
        print("File not found.")
        return

    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    messages = data.get('messages', [])
    print(f"Processing {len(messages)} messages...")
    
    cleaned_count = 0
    new_messages = []
    
    for m in messages:
        content = m.get('content', '')
        # If content is a list (tool calls), check its string representation
        if isinstance(content, list):
            content_str = json.dumps(content)
        else:
            content_str = str(content)
            
        if len(content_str) > 50000:
            print(f"Found massive message ({len(content_str)} chars). Truncating...")
            if isinstance(content, list):
                # Truncate the first element if it's a list
                m['content'] = [str(content[0])[:10000] + "... [TRUNCATED BY ANTIGRAVITY] ..."]
            else:
                m['content'] = content_str[:10000] + "... [TRUNCATED BY ANTIGRAVITY] ..."
            cleaned_count += 1
        new_messages.append(m)
        
    data['messages'] = new_messages
    
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
        
    print(f"Success. Cleaned {cleaned_count} messages.")

if __name__ == "__main__":
    clean_checkpoint("reports/backend_20260504_224610/checkpoint.json")
