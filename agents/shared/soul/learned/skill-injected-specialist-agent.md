When adding a new specialist agent to a multi-agent system, follow this four-part pattern:

1. **Handler with skill auto-loading**: Create `agents/<domain>/handler.py` with a standard `handle(workspace, task_id, content, sender, thread_id, ...)` signature. Include a `_load_skills()` function that globs `skills/*.md` from the agent's own directory, reads each file, and joins them with separators. This makes the agent's domain knowledge modular — add/remove/edit markdown files to change capabilities without touching code.

2. **Dedicated prompt function**: Add a `<domain>_prompt()` to the shared prompts module. Structure it as: identity context → skill/framework context (with instruction to apply selectively, not force every framework) → task details → output instructions. The prompt should guide the agent on *when* to apply which skill, not just dump them all.

3. **Router registration**: In the task planner/router, add the new agent type to: (a) the LLM planner's available agents list with clear trigger descriptions, (b) the valid agents set for validation, (c) the execution dispatch switch with a `_handle_<domain>()` function.

4. **Handler wiring**: The dispatch function uses `importlib.util.spec_from_file_location` to dynamically load the handler module, avoiding circular imports and keeping the agent directory self-contained. Pass thread history/memory for conversational context.

Key design principles:
- Skills are plain markdown files — human-readable, version-controllable, easy to add
- The agent selectively applies frameworks rather than forcing all of them
- Standard handler signature enables uniform dispatch and multi-step chaining
- Each agent directory is self-contained (handler.py + skills/) — plug-and-play