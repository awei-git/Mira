# operational-tasks-skip-output-pipeline

File system operations and other pure side-effect tasks must not be routed through content-generation task pipelines that expect output.md or quality checks

**Source**: Extracted from task failure (2026-03-25)
**Tags**: task-routing, pipeline, operational-tasks, error-handling, agent-framework

---

## Rule: Operational tasks must bypass content-generation pipelines

When a todo/task request is purely operational — file deletion, renaming, moving, running a script, sending a message — do not route it through any pipeline that:
- Generates an `output.md` or equivalent artifact
- Runs output quality checks (length, format, completeness)
- Expects a content deliverable as the task result

These pipelines are designed for content-generation tasks (transcripts, essays, audio files). Applying them to operational tasks produces nonsensical errors like `Output quality check failed: Output is empty or too short` because there is no content to check — the task result is a side effect, not a document.

**How to classify at task intake:**
- If the task verb is: delete, move, rename, run, send, install, restart → operational, no output pipeline
- If the task verb is: generate, write, transcribe, summarize, create → content task, use pipeline

**When an internal framework error occurs:** Do not ask the user what system or script caused it. You know what framework you're running in. Diagnose internally first. Asking the user `能把具体的脚本或报错上下文贴给我看看吗？` for an error your own pipeline threw is externalizing your own internal confusion — it wastes user time and erodes trust.

**Recovery:** If caught mid-execution in the wrong pipeline, abort the pipeline cleanly, complete the actual task directly, and acknowledge the framework mismatch briefly.
