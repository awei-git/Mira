"""Plan execution loop — execute multi-step plans.

Extracted from task_worker.py. Uses task_result + task_support.
"""

import json
import logging
import shutil
import sys
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent
if str(_AGENTS_DIR.parent / "lib") not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))
if str(_AGENTS_DIR / "writer") not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR / "writer"))
if str(_AGENTS_DIR / "general") not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR / "general"))

from config import DEFAULT_MODEL
from execution.runtime_contract import normalize_task_status
from execution.calibration import _record_premortem, _record_postmortem
from execution.plan_state import initialize_plan_artifacts, mark_step_finished, mark_step_running
from planning.planner import _synthesize_outputs

LOCAL_ONLY_AGENTS: frozenset[str] = frozenset({"secret", "health"})
LOCAL_MODEL_PATTERNS: tuple[str, ...] = ("mlx", "local", "ollama", "omlx")

from task_support import (
    _append_exec_log,
    _get_round_num,
    _verify_output,
    _result_metadata,
    _safe_general_fallback,
    _register_runtime_tools_created,
    _invoke_registry_handler,
    _invoke_registry_preflight,
    _enrich_plan_with_runtime_policy,
)
from task_result import (
    _write_result,
    _snapshot_file,
    _ensure_step_result,
)

log = logging.getLogger("task_worker")


def _execute_plan(
    plan: list[dict],
    workspace: Path,
    task_id: str,
    content: str,
    sender: str,
    thread_id: str,
    user_id: str = "ang",
    allowed_agents: list | None = None,
    content_filter: bool = False,
    model_restriction: str | None = None,
    workflow_id: str = "",
):
    """Execute a multi-step plan. Each step's output feeds into the next."""
    from task_worker import _Heartbeat, _set_streaming_task_id, _emit_status

    initialize_plan_artifacts(
        workspace,
        task_id=task_id,
        workflow_id=workflow_id,
        user_id=user_id,
        request=content,
        plan=plan,
    )
    prev_output = None
    is_multi = len(plan) > 1
    round_num = _get_round_num(workspace)

    heartbeat = _Heartbeat(task_id)
    heartbeat.start()
    try:
        _execute_plan_steps(
            plan,
            workspace,
            task_id,
            content,
            sender,
            thread_id,
            prev_output,
            is_multi,
            round_num,
            user_id=user_id,
            allowed_agents=allowed_agents or [],
            content_filter=content_filter,
            model_restriction=model_restriction,
            workflow_id=workflow_id,
        )
    finally:
        heartbeat.stop()


def _execute_plan_steps(
    plan,
    workspace,
    task_id,
    content,
    sender,
    thread_id,
    prev_output,
    is_multi,
    round_num,
    *,
    user_id: str = "ang",
    allowed_agents: list | None = None,
    content_filter: bool = False,
    model_restriction: str | None = None,
    workflow_id: str = "",
):
    """Inner loop extracted so heartbeat can be stopped in finally block."""
    from task_worker import _set_streaming_task_id, _emit_status, emit_progress

    _set_streaming_task_id(task_id)
    if not (workspace / "step_states.json").exists():
        initialize_plan_artifacts(
            workspace,
            task_id=task_id,
            workflow_id=workflow_id,
            user_id=user_id,
            request=content,
            plan=plan,
        )
    from agent_registry import get_registry

    registry = get_registry()
    step_count = len(plan)

    for i, step in enumerate(plan):
        declared_agent = step["agent"]
        execution_agent = declared_agent
        step.setdefault("capability_class", registry.get_capability_class(declared_agent))
        step.setdefault("policy", registry.get_capability_policy(declared_agent))
        policy = step["policy"]
        capability_class = step["capability_class"]
        instruction = step["instruction"]
        tier = step.get("tier", "light")
        prediction = step.get("prediction")
        is_last = i == len(plan) - 1
        log.info(
            "Step %d/%d: agent=%s tier=%s capability=%s instruction=%s",
            i + 1,
            len(plan),
            declared_agent,
            tier,
            capability_class,
            instruction[:80],
        )

        _record_premortem(task_id, i, declared_agent, instruction, prediction)

        if prev_output and declared_agent != "clarify":
            instruction = f"{instruction}\n\n--- 上一步的输出 ---\n{prev_output[:3000]}"

        _step_icons = {
            "briefing": ("Fetching feeds...", "newspaper"),
            "writing": ("Writing...", "doc.text"),
            "publish": ("Publishing...", "paperplane"),
            "analyst": ("Analyzing...", "chart.bar"),
            "video": ("Processing video...", "film"),
            "photo": ("Editing photo...", "camera"),
            "podcast": ("Generating audio...", "waveform"),
            "socialmedia": ("Checking Substack...", "at"),
            "surfer": ("Browsing...", "globe"),
            "discussion": ("Thinking...", "bubble.left.and.text.bubble.right"),
            "general": ("Working...", "gear"),
            "secret": ("Private mode...", "lock.shield"),
            "clarify": ("Need your input", "questionmark.bubble"),
        }
        status_text, status_icon = _step_icons.get(declared_agent, ("Working...", "gear"))
        if is_multi:
            status_text = f"Step {i+1}/{len(plan)}: {status_text}"
        _emit_status(task_id, status_text, status_icon)
        mark_step_running(
            workspace,
            step_index=i,
            declared_agent=declared_agent,
            execution_agent=execution_agent,
            input_summary=instruction,
        )

        if declared_agent == "clarify":
            (workspace / "output.md").write_text(instruction, encoding="utf-8")
            _write_result(
                workspace,
                task_id,
                "needs-input",
                instruction,
                tags=["clarify"],
                metadata=_result_metadata(
                    step,
                    step_index=i,
                    step_count=step_count,
                    declared_agent=declared_agent,
                    execution_agent=execution_agent,
                ),
                failure_class="needs_input",
                next_action="await-user-input",
            )
            mark_step_finished(
                workspace,
                step_index=i,
                status="needs-input",
                declared_agent=declared_agent,
                execution_agent=execution_agent,
                output_summary=instruction,
            )
            _append_exec_log(workspace, round_num, "clarify", "needs-input", instruction)
            return

        if allowed_agents and declared_agent not in allowed_agents and declared_agent not in ("clarify", "discussion"):
            log.warning(
                "ACCESS DENIED: user=%s agent=%s not in allowed_agents=%s", user_id, declared_agent, allowed_agents
            )
            denied_msg = (
                f"Sorry, you don't have access to the {declared_agent} agent. "
                f"Available: {', '.join(allowed_agents)}"
            )
            _write_result(
                workspace,
                task_id,
                "blocked",
                denied_msg,
                metadata=_result_metadata(
                    step,
                    step_index=i,
                    step_count=step_count,
                    declared_agent=declared_agent,
                    execution_agent=execution_agent,
                ),
                failure_class="access_denied",
                next_action="await-user-input",
            )
            mark_step_finished(
                workspace,
                step_index=i,
                status="blocked",
                declared_agent=declared_agent,
                execution_agent=execution_agent,
                failure_reason=denied_msg,
            )
            return

        if content_filter:
            from config import CHILD_SAFETY_PROMPT

            instruction = f"{CHILD_SAFETY_PROMPT}\n\n---\n\n{instruction}"

        if declared_agent in LOCAL_ONLY_AGENTS:
            resolved_model = model_restriction or DEFAULT_MODEL
            if not any(p in resolved_model.lower() for p in LOCAL_MODEL_PATTERNS):
                err = f"Refused to route task to {declared_agent}: cloud model detected, local-only policy violated."
                log.error(
                    "LOCAL_ONLY_POLICY: %s (model_restriction=%r, resolved=%r)", err, model_restriction, resolved_model
                )
                raise RuntimeError(err)

        from llm import set_usage_agent, set_model_policy

        if model_restriction:
            set_model_policy(model_restriction)
            log.info("Model policy: %s for user=%s", model_restriction, user_id)
        else:
            set_model_policy(None)

        requires_preflight = bool(policy.get("requires_preflight"))
        fail_closed = bool(policy.get("fail_closed"))
        allow_fallback = bool(policy.get("allow_fallback_to_general"))
        set_usage_agent(declared_agent)

        output_file = workspace / "output.md"
        result_file = workspace / "result.json"
        output_snapshot = _snapshot_file(output_file)
        handler_result = None
        used_fallback = False
        preflight_fn = None

        try:
            try:
                preflight_fn = getattr(registry, "load_preflight", lambda name: None)(declared_agent)
            except KeyError:
                if requires_preflight or fail_closed:
                    preflight_msg = f"{declared_agent} preflight missing from registry"
                    log.error("%s", preflight_msg)
                    (workspace / "output.md").write_text(preflight_msg, encoding="utf-8")
                    _write_result(
                        workspace,
                        task_id,
                        "blocked",
                        preflight_msg,
                        agent=declared_agent,
                        metadata=_result_metadata(
                            step,
                            step_index=i,
                            step_count=step_count,
                            declared_agent=declared_agent,
                            execution_agent=execution_agent,
                        ),
                        failure_class="preflight_blocked",
                        next_action="resolve-preflight-block",
                    )
                    mark_step_finished(
                        workspace,
                        step_index=i,
                        status="blocked",
                        declared_agent=declared_agent,
                        execution_agent=execution_agent,
                        failure_reason=preflight_msg,
                    )
                    return
                log.warning("Agent '%s' not in registry during preflight load, falling back to general", declared_agent)
                execution_agent = "general"
                if not _safe_general_fallback(
                    workspace,
                    task_id,
                    instruction,
                    sender,
                    thread_id,
                    tier=tier,
                    step=step,
                    step_index=i,
                    step_count=step_count,
                    declared_agent=declared_agent,
                    execution_agent=execution_agent,
                    workflow_id=workflow_id,
                ):
                    return
                used_fallback = True
            except ImportError as e:
                if fail_closed:
                    preflight_msg = f"{declared_agent} preflight load failed: {e}"
                    log.error("%s", preflight_msg)
                    (workspace / "output.md").write_text(preflight_msg, encoding="utf-8")
                    _write_result(
                        workspace,
                        task_id,
                        "blocked",
                        preflight_msg,
                        agent=declared_agent,
                        metadata=_result_metadata(
                            step,
                            step_index=i,
                            step_count=step_count,
                            declared_agent=declared_agent,
                            execution_agent=execution_agent,
                        ),
                        failure_class="preflight_blocked",
                        next_action="resolve-preflight-block",
                    )
                    mark_step_finished(
                        workspace,
                        step_index=i,
                        status="blocked",
                        declared_agent=declared_agent,
                        execution_agent=execution_agent,
                        failure_reason=preflight_msg,
                    )
                    return
                log.error(
                    "ImportError loading preflight for agent '%s': %s — falling back to general", declared_agent, e
                )
                execution_agent = "general"
                if not _safe_general_fallback(
                    workspace,
                    task_id,
                    instruction,
                    sender,
                    thread_id,
                    tier=tier,
                    step=step,
                    step_index=i,
                    step_count=step_count,
                    declared_agent=declared_agent,
                    execution_agent=execution_agent,
                    workflow_id=workflow_id,
                ):
                    return
                used_fallback = True
            except Exception as e:
                if fail_closed:
                    preflight_msg = f"{declared_agent} preflight load failed: {e}"
                    log.error("%s", preflight_msg)
                    (workspace / "output.md").write_text(preflight_msg, encoding="utf-8")
                    _write_result(
                        workspace,
                        task_id,
                        "blocked",
                        preflight_msg,
                        agent=declared_agent,
                        metadata=_result_metadata(
                            step,
                            step_index=i,
                            step_count=step_count,
                            declared_agent=declared_agent,
                            execution_agent=execution_agent,
                        ),
                        failure_class="preflight_blocked",
                        next_action="resolve-preflight-block",
                    )
                    mark_step_finished(
                        workspace,
                        step_index=i,
                        status="blocked",
                        declared_agent=declared_agent,
                        execution_agent=execution_agent,
                        failure_reason=preflight_msg,
                    )
                    return
                log.error("Registry preflight for '%s' failed to load: %s — falling back to general", declared_agent, e)
                execution_agent = "general"
                if not _safe_general_fallback(
                    workspace,
                    task_id,
                    instruction,
                    sender,
                    thread_id,
                    tier=tier,
                    step=step,
                    step_index=i,
                    step_count=step_count,
                    declared_agent=declared_agent,
                    execution_agent=execution_agent,
                    workflow_id=workflow_id,
                ):
                    return
                used_fallback = True

            if not used_fallback and not preflight_fn and requires_preflight:
                preflight_msg = f"{declared_agent} preflight missing"
                log.error("%s", preflight_msg)
                (workspace / "output.md").write_text(preflight_msg, encoding="utf-8")
                _write_result(
                    workspace,
                    task_id,
                    "blocked",
                    preflight_msg,
                    agent=declared_agent,
                    metadata=_result_metadata(
                        step,
                        step_index=i,
                        step_count=step_count,
                        declared_agent=declared_agent,
                        execution_agent=execution_agent,
                    ),
                    failure_class="preflight_blocked",
                    next_action="resolve-preflight-block",
                )
                mark_step_finished(
                    workspace,
                    step_index=i,
                    status="blocked",
                    declared_agent=declared_agent,
                    execution_agent=execution_agent,
                    failure_reason=preflight_msg,
                )
                return

            if not used_fallback and preflight_fn:
                try:
                    passed, preflight_msg = _invoke_registry_preflight(
                        preflight_fn,
                        workspace,
                        task_id,
                        instruction,
                        sender,
                        thread_id,
                        tier,
                        user_id=user_id,
                    )
                except Exception as e:
                    preflight_msg = f"{declared_agent} preflight failed: {e}"
                    log.error("Preflight for '%s' raised: %s", declared_agent, e)
                    (workspace / "output.md").write_text(preflight_msg, encoding="utf-8")
                    _write_result(
                        workspace,
                        task_id,
                        "error",
                        preflight_msg,
                        agent=declared_agent,
                        metadata=_result_metadata(
                            step,
                            step_index=i,
                            step_count=step_count,
                            declared_agent=declared_agent,
                            execution_agent=execution_agent,
                        ),
                        failure_class="preflight_error",
                    )
                    mark_step_finished(
                        workspace,
                        step_index=i,
                        status="failed",
                        declared_agent=declared_agent,
                        execution_agent=execution_agent,
                        failure_reason=preflight_msg,
                    )
                    return
                if not passed:
                    log.warning("Preflight blocked agent '%s': %s", declared_agent, preflight_msg)
                    (workspace / "output.md").write_text(preflight_msg, encoding="utf-8")
                    _write_result(
                        workspace,
                        task_id,
                        "blocked",
                        preflight_msg,
                        agent=declared_agent,
                        metadata=_result_metadata(
                            step,
                            step_index=i,
                            step_count=step_count,
                            declared_agent=declared_agent,
                            execution_agent=execution_agent,
                        ),
                        failure_class="preflight_blocked",
                        next_action="resolve-preflight-block",
                    )
                    mark_step_finished(
                        workspace,
                        step_index=i,
                        status="blocked",
                        declared_agent=declared_agent,
                        execution_agent=execution_agent,
                        failure_reason=preflight_msg,
                    )
                    return

            if not used_fallback:
                try:
                    handler_fn = registry.load_handler(declared_agent)
                    handler_result = _invoke_registry_handler(
                        handler_fn,
                        workspace,
                        task_id,
                        instruction,
                        sender,
                        thread_id,
                        tier,
                        user_id=user_id,
                        agent_id=declared_agent,
                    )
                except KeyError as e:
                    handler_msg = f"{declared_agent} handler missing from registry"
                    if fail_closed or not allow_fallback:
                        log.error("%s", handler_msg)
                        _write_result(
                            workspace,
                            task_id,
                            "error",
                            handler_msg,
                            agent=declared_agent,
                            metadata=_result_metadata(
                                step,
                                step_index=i,
                                step_count=step_count,
                                declared_agent=declared_agent,
                                execution_agent=execution_agent,
                            ),
                            failure_class="handler_error",
                        )
                        mark_step_finished(
                            workspace,
                            step_index=i,
                            status="failed",
                            declared_agent=declared_agent,
                            execution_agent=execution_agent,
                            failure_reason=handler_msg,
                        )
                        return
                    log.warning("Agent '%s' not in registry, falling back to general: %s", declared_agent, e)
                    execution_agent = "general"
                    if not _safe_general_fallback(
                        workspace,
                        task_id,
                        instruction,
                        sender,
                        thread_id,
                        tier=tier,
                        step=step,
                        step_index=i,
                        step_count=step_count,
                        declared_agent=declared_agent,
                        execution_agent=execution_agent,
                        workflow_id=workflow_id,
                    ):
                        return
                except ImportError as e:
                    handler_msg = f"{declared_agent} handler load failed: {e}"
                    if fail_closed or not allow_fallback:
                        log.error("%s", handler_msg)
                        _write_result(
                            workspace,
                            task_id,
                            "error",
                            handler_msg,
                            agent=declared_agent,
                            metadata=_result_metadata(
                                step,
                                step_index=i,
                                step_count=step_count,
                                declared_agent=declared_agent,
                                execution_agent=execution_agent,
                            ),
                            failure_class="handler_error",
                        )
                        mark_step_finished(
                            workspace,
                            step_index=i,
                            status="failed",
                            declared_agent=declared_agent,
                            execution_agent=execution_agent,
                            failure_reason=handler_msg,
                        )
                        return
                    log.error("ImportError loading agent '%s': %s — falling back to general", declared_agent, e)
                    execution_agent = "general"
                    if not _safe_general_fallback(
                        workspace,
                        task_id,
                        instruction,
                        sender,
                        thread_id,
                        tier=tier,
                        step=step,
                        step_index=i,
                        step_count=step_count,
                        declared_agent=declared_agent,
                        execution_agent=execution_agent,
                        workflow_id=workflow_id,
                    ):
                        return
                except Exception as e:
                    handler_msg = f"{declared_agent} handler failed to load: {e}"
                    if fail_closed or not allow_fallback:
                        log.error("%s", handler_msg)
                        _write_result(
                            workspace,
                            task_id,
                            "error",
                            handler_msg,
                            agent=declared_agent,
                            metadata=_result_metadata(
                                step,
                                step_index=i,
                                step_count=step_count,
                                declared_agent=declared_agent,
                                execution_agent=execution_agent,
                            ),
                            failure_class="handler_error",
                        )
                        mark_step_finished(
                            workspace,
                            step_index=i,
                            status="failed",
                            declared_agent=declared_agent,
                            execution_agent=execution_agent,
                            failure_reason=handler_msg,
                        )
                        return
                    log.error("Registry handler for '%s' failed: %s — falling back to general", declared_agent, e)
                    execution_agent = "general"
                    if not _safe_general_fallback(
                        workspace,
                        task_id,
                        instruction,
                        sender,
                        thread_id,
                        tier=tier,
                        step=step,
                        step_index=i,
                        step_count=step_count,
                        declared_agent=declared_agent,
                        execution_agent=execution_agent,
                        workflow_id=workflow_id,
                    ):
                        return
        finally:
            set_model_policy(None)  # always reset after step

        _ensure_step_result(
            workspace,
            task_id,
            execution_agent,
            content,
            handler_result,
            output_snapshot,
            metadata=_result_metadata(
                step,
                step_index=i,
                step_count=step_count,
                declared_agent=declared_agent,
                execution_agent=execution_agent,
            ),
        )
        step_status = "done"
        step_output_preview = ""
        step_retry_count = 0
        if result_file.exists():
            try:
                r = json.loads(result_file.read_text(encoding="utf-8"))
                changed = False
                if "agent" not in r:
                    r["agent"] = execution_agent
                    changed = True
                if "declared_agent" not in r:
                    r["declared_agent"] = declared_agent
                    changed = True
                if "execution_agent" not in r:
                    r["execution_agent"] = execution_agent
                    changed = True
                if "capability_class" not in r:
                    r["capability_class"] = capability_class
                    changed = True
                if "policy" not in r:
                    r["policy"] = policy
                    changed = True
                if changed:
                    result_file.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
                execution_agent = r.get("agent", execution_agent)
                step_status = normalize_task_status(r.get("status", "done"))
                step_output_preview = r.get("summary", "")[:200]
                step_retry_count = int(r.get("retry_count", 0) or 0)
                if step_status in ("failed", "blocked", "needs-input"):
                    outcome = "failed" if step_status == "failed" else step_status
                    _record_postmortem(task_id, i, declared_agent, prediction, outcome, step_output_preview)
                    _append_exec_log(workspace, round_num, execution_agent, step_status, r.get("summary", ""))
                    mark_step_finished(
                        workspace,
                        step_index=i,
                        status=step_status,
                        declared_agent=declared_agent,
                        execution_agent=execution_agent,
                        output_summary=step_output_preview,
                        failure_reason=r.get("summary", "") if step_status != "needs-input" else "",
                        retry_count=step_retry_count,
                    )
                    log.error(
                        "Step %d/%d stopped plan with status=%s: %s",
                        i + 1,
                        len(plan),
                        step_status,
                        r.get("summary", ""),
                    )
                    return
            except (json.JSONDecodeError, OSError):
                pass

        if output_file.exists():
            prev_output = output_file.read_text(encoding="utf-8")
            step_output_preview = prev_output[:200]
            verification = _verify_output(prev_output, workspace)
            if verification:
                log.warning("HALLUCINATION DETECTED: %s", verification)
                prev_output += f"\n\n⚠️ VERIFICATION FAILED: {verification}"
                _append_exec_log(workspace, round_num, execution_agent, "unverified", f"HALLUCINATION: {verification}")
                _record_postmortem(task_id, i, declared_agent, prediction, "hallucination", step_output_preview)
            else:
                _append_exec_log(workspace, round_num, execution_agent, "done", prev_output[:300])
                _record_postmortem(task_id, i, declared_agent, prediction, "done", step_output_preview)
            if not is_last and prev_output.strip():
                snippet = prev_output.strip()[:300]
                emit_progress(f"Step {i+1} done: {snippet}", "checkmark.circle")
            numbered = workspace / f"output_r{round_num}.md"
            shutil.copy2(output_file, numbered)

        mark_step_finished(
            workspace,
            step_index=i,
            status="done",
            declared_agent=declared_agent,
            execution_agent=execution_agent,
            output_summary=step_output_preview,
            retry_count=step_retry_count,
        )

        if is_multi and not is_last and result_file.exists():
            result_file.unlink()

    if is_multi and prev_output:
        synthesized = _synthesize_outputs(content, plan, prev_output)
        if synthesized:
            (workspace / "output.md").write_text(synthesized, encoding="utf-8")
            prev_output = synthesized

    try:
        _register_runtime_tools_created(workspace)
    except Exception as e:
        log.warning("Runtime tool registration failed: %s", e)

    log.info("Plan execution complete (%d steps)", len(plan))
