from pathlib import Path

path = Path('core/content_manager.py')
content = path.read_text(encoding='utf-8')

target = "    return final_messages, stats"
if target not in content:
    print("Error: Target not found.")
else:
    replacement = """    stats["output_messages"] = len(final_messages)
    stats["output_tokens_est"] = sum(_msg_tokens(m) for m in final_messages)
    
    # EMERGENCY RECURSIVE PRUNING
    # If we are still over the safe limit (240k), we MUST drop messages starting from the oldest.
    # This is a last resort to prevent a 400 Bad Request.
    SAFETY_LIMIT = 240000 
    
    # Indices 0 and 1 are System and Memory, we shouldn't drop those unless absolutely forced.
    while stats["output_tokens_est"] > SAFETY_LIMIT and len(final_messages) > 3:
        dropped = final_messages.pop(2)
        stats["output_tokens_est"] -= _msg_tokens(dropped)
        stats["cycles_dropped"] += 1

    return final_messages, stats"""
    
    new_content = content.replace(target, replacement)
    path.write_text(new_content, encoding='utf-8')
    print("Emergency Pruning added successfully.")
