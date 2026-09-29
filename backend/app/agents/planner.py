def build_plan(intent: dict) -> list[dict]:
    plan = [{"name": "Understand request", "type": "understand"}]
    if intent.get("requires_memory"):
        plan.append({"name": "Retrieve memory", "type": "memory"})
    tools = intent.get("tools", [])
    for tool_name in tools:
        plan.append(
            {"name": f"Execute {tool_name}", "type": "tool", "tool": tool_name}
        )
    if tools:
        plan.append({"name": "Verify results", "type": "verify"})
    plan.append({"name": "Compose response", "type": "respond"})
    plan.append({"name": "Update memory", "type": "memory_update"})
    return plan
