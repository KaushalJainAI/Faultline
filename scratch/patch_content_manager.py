import sys
from pathlib import Path

path = Path('core/content_manager.py')
content = path.read_text(encoding='utf-8')

start_marker = 'stats["windowing_applied"] = True'
end_marker = 'return tier3_msgs + tier2_msgs + tier1_msgs, stats'

start_idx = content.find(start_marker)
end_idx = content.find(end_marker) + len(end_marker)

if start_idx == -1 or end_idx == -1:
    print(f"Error: Markers not found. Start: {start_idx}, End: {end_idx}")
    sys.exit(1)

replacement = """stats["windowing_applied"] = True
    
    # VIRTUAL MEMORY ARCHIVAL ENGINE
    # ------------------------------------------------------------------
    # Tier 1 (Turn Age <= 3): Full Fidelity
    # Tier 2 (Turn Age <= 8): Summary + Vault Reference
    # Tier 3 (Turn Age > 8): Archived (Deep Archive)
    
    final_messages = [system_msg]
    if memory_msg:
        final_messages.append(memory_msg)
    
    # Work backwards to tag turn ages
    msg_metadata = []
    current_turn_age = 1
    for m in reversed(clipped_messages):
        if isinstance(m, HumanMessage):
            current_turn_age += 1
        msg_metadata.append({"msg": m, "age": current_turn_age, "index": len(msg_metadata)})
    
    msg_metadata.reverse()
    
    for item in msg_metadata:
        m = item["msg"]
        age = item["age"]
        m_id = f"msg_turn_{age}_{item['index']}"
        
        if age <= 3:
            final_messages.append(m)
        elif age <= 8:
            _archive_message(m, run_folder, m_id)
            summary = _summarize_message(m)
            marker_text = f"\\n[SUMMARY: {summary}]\\n[VAULT REF: {m_id}. Use retrieve_history_message('{m_id}') to recall full text.]"
            
            if isinstance(m, AIMessage):
                final_messages.append(AIMessage(content=marker_text, tool_calls=getattr(m, 'tool_calls', [])))
            elif isinstance(m, ToolMessage):
                final_messages.append(ToolMessage(content=marker_text, tool_call_id=m.tool_call_id))
            elif isinstance(m, HumanMessage):
                final_messages.append(HumanMessage(content=marker_text))
            else:
                final_messages.append(SystemMessage(content=marker_text))
            stats["cycles_compressed"] += 1
        else:
            _archive_message(m, run_folder, m_id)
            stats["cycles_dropped"] += 1
    
    stats["output_messages"] = len(final_messages)
    stats["output_tokens_est"] = sum(_msg_tokens(m) for m in final_messages)
    return final_messages, stats"""

new_content = content[:start_idx] + replacement + content[end_idx:]
path.write_text(new_content, encoding='utf-8')
print("Successfully patched content_manager.py")
