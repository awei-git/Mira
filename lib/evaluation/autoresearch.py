"""AutoResearch loop — optimize prompts, skills, and workflows via iterative experimentation.

Implements Karpathy's autoresearch pattern adapted for our systems:
- Editable asset (prompt/config/skill) + scalar metric + time-boxed cycle
- LLM-as-judge evaluation with cross-model bias prevention
- A/B blind comparison for relative quality assessment
- Experiment history tracking for cumulative learning

Usage:
    from autoresearch import AutoResearchLoop

    loop = AutoResearchLoop(
        name="tetra-debate-macro-prompt",
        asset_path=Path("prompts/macro_system.txt"),
        eval_fn=my_evaluation_function,
        mutate_model="claude",
        judge_model="gpt5",
    )
    result = loop.run(max_iterations=10, time_budget_minutes=60)
"""
import json
import logging
import random
import re
import time
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Any, Optional

log = logging.getLogger("autoresearch")

# ---------------------------------------------------------------------------
# Evaluation primitives
# ---------------------------------------------------------------------------

@dataclass
class EvalResult:
    """Result of evaluating a single output."""
    scores: dict[str, float]       # criterion -> 0-10 score
    reasoning: str = ""
    aggregate: float = 0.0         # weighted mean of scores
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.scores and not self.aggregate:
            self.aggregate = sum(self.scores.values()) / len(self.scores)


@dataclass
class CompareResult:
    """Result of blind A/B comparison."""
    winner: str                    # "a", "b", or "tie"
    confidence: float              # 0-1
    reasoning: str = ""
    per_criterion: dict = field(default_factory=dict)


@dataclass
class Experiment:
    """Record of a single autoresearch experiment."""
    iteration: int
    hypothesis: str
    asset_diff: str                # human-readable description of change
    eval_result: Optional[EvalResult] = None
    compare_result: Optional[CompareResult] = None
    kept: bool = False
    timestamp: str = ""
    duration_s: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


# ---------------------------------------------------------------------------
# LLM-as-Judge
# ---------------------------------------------------------------------------

def llm_judge(
    output: str,
    criteria: dict[str, str],
    rubric: str = "",
    model_fn: Callable = None,
) -> EvalResult:
    """Score an output on multiple criteria using an LLM judge.

    Args:
        output: The text to evaluate
        criteria: {criterion_name: description_of_what_10_looks_like}
        rubric: Optional scoring anchors (what each score level means)
        model_fn: fn(prompt, timeout) -> str  (e.g. claude_think or model_think)
    """
    if not model_fn:
        from sub_agent import claude_think
        model_fn = lambda p, t=120: claude_think(p, timeout=t)

    criteria_block = "\n".join(
        f"- **{name}**: 10/10 = {desc}" for name, desc in criteria.items()
    )

    rubric_block = rubric or """Score anchors:
- 1-2: Fundamentally broken or missing
- 3-4: Present but weak, major issues
- 5-6: Acceptable, does the job
- 7-8: Good, clear quality
- 9-10: Exceptional, hard to improve"""

    prompt = f"""You are an expert evaluator. Score the following output on each criterion.
Be rigorous and honest — most work is 5-7, not 9-10.

=== CRITERIA ===
{criteria_block}

=== SCORING RUBRIC ===
{rubric_block}

=== OUTPUT TO EVALUATE ===
{output[:8000]}

=== INSTRUCTIONS ===
For each criterion, give a score (0-10) and a one-sentence justification.
Then give an overall assessment.

Respond with JSON only:
{{
    "scores": {{
        "criterion_name": {{"score": 7.5, "reason": "one sentence"}},
        ...
    }},
    "overall": "2-3 sentence summary of strengths and weaknesses"
}}"""

    raw = model_fn(prompt, 120)
    if not raw:
        return EvalResult(scores={k: 5.0 for k in criteria}, reasoning="Judge failed to respond")

    try:
        match = re.search(r'\{[\s\S]+\}', raw)
        if not match:
            return EvalResult(scores={k: 5.0 for k in criteria}, reasoning="Failed to parse judge output")
        json_str = match.group()
        # Clean common JSON issues from LLMs (trailing commas, comments)
        json_str = re.sub(r',\s*}', '}', json_str)
        json_str = re.sub(r',\s*]', ']', json_str)
        json_str = re.sub(r'//[^\n]*', '', json_str)
        data = json.loads(json_str)
        scores = {}
        for name in criteria:
            entry = data.get("scores", {}).get(name, {})
            if isinstance(entry, dict):
                scores[name] = float(entry.get("score", 5.0))
            elif isinstance(entry, (int, float)):
                scores[name] = float(entry)
            else:
                scores[name] = 5.0
        return EvalResult(
            scores=scores,
            reasoning=data.get("overall", ""),
        )
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        log.warning("Judge parse error: %s", e)
        return EvalResult(scores={k: 5.0 for k in criteria}, reasoning=f"Parse error: {e}")


def llm_compare(
    output_a: str,
    output_b: str,
    criteria: dict[str, str],
    model_fn: Callable = None,
) -> CompareResult:
    """Blind A/B comparison of two outputs.

    Randomly assigns labels to prevent position bias.
    Uses a different model than the generator when possible.
    """
    if not model_fn:
        from sub_agent import claude_think
        model_fn = lambda p, t=120: claude_think(p, timeout=t)

    # Randomize which is "Option 1" vs "Option 2" to prevent position bias
    swap = random.random() < 0.5
    if swap:
        first, second = output_b, output_a
    else:
        first, second = output_a, output_b

    criteria_block = "\n".join(
        f"- **{name}**: {desc}" for name, desc in criteria.items()
    )

    prompt = f"""You are comparing two outputs. Determine which is better.
You do NOT know which is the baseline and which is the candidate.
Judge purely on quality.

=== CRITERIA ===
{criteria_block}

=== OPTION 1 ===
{first[:6000]}

=== OPTION 2 ===
{second[:6000]}

=== INSTRUCTIONS ===
Compare on each criterion. Then pick a winner.
Be decisive — "tie" only if genuinely indistinguishable.

Respond with JSON only:
{{
    "per_criterion": {{
        "criterion_name": {{"winner": "1" or "2" or "tie", "reason": "one sentence"}}
    }},
    "overall_winner": "1" or "2" or "tie",
    "confidence": 0.0 to 1.0,
    "reasoning": "2-3 sentences explaining the decision"
}}"""

    raw = model_fn(prompt, 120)
    if not raw:
        return CompareResult(winner="tie", confidence=0.0, reasoning="Comparator failed")

    try:
        match = re.search(r'\{[\s\S]+\}', raw)
        if not match:
            return CompareResult(winner="tie", confidence=0.0, reasoning="Parse failed")
        json_str = match.group()
        json_str = re.sub(r',\s*}', '}', json_str)
        json_str = re.sub(r',\s*]', ']', json_str)
        json_str = re.sub(r'//[^\n]*', '', json_str)
        data = json.loads(json_str)

        raw_winner = str(data.get("overall_winner", "tie"))
        # Map "1"/"2" back to "a"/"b" accounting for swap
        if raw_winner == "1":
            winner = "b" if swap else "a"
        elif raw_winner == "2":
            winner = "a" if swap else "b"
        else:
            winner = "tie"

        return CompareResult(
            winner=winner,
            confidence=float(data.get("confidence", 0.5)),
            reasoning=data.get("reasoning", ""),
            per_criterion=data.get("per_criterion", {}),
        )
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        log.warning("Compare parse error: %s", e)
        return CompareResult(winner="tie", confidence=0.0, reasoning=f"Parse error: {e}")


# ---------------------------------------------------------------------------
# Mutation engine
# ---------------------------------------------------------------------------

def mutate_asset(
    current_asset: str,
    eval_feedback: str,
    history_summary: str,
    directive: str,
    model_fn: Callable = None,
) -> tuple[str, str, str]:
    """Propose a mutation to the asset based on evaluation feedback.

    Returns: (new_asset, hypothesis, diff_description)
    """
    if not model_fn:
        from sub_agent import claude_think
        model_fn = lambda p, t=120: claude_think(p, timeout=t)

    prompt = f"""You are optimizing a text asset (prompt, skill definition, or config).

=== OPTIMIZATION DIRECTIVE ===
{directive}

=== CURRENT ASSET ===
{current_asset}

=== EVALUATION FEEDBACK (most recent) ===
{eval_feedback}

=== EXPERIMENT HISTORY (what we've tried) ===
{history_summary or "No prior experiments."}

=== INSTRUCTIONS ===
1. Form a specific hypothesis about what change would improve the asset
2. Make the change — output the COMPLETE new version (not a diff)
3. Changes should be targeted, not wholesale rewrites
4. Learn from experiment history — don't repeat failed approaches

Respond with JSON only:
{{
    "hypothesis": "what you think will improve and why",
    "diff_description": "brief description of what changed",
    "new_asset": "the complete modified asset text"
}}"""

    raw = model_fn(prompt, 180)
    if not raw:
        return current_asset, "mutation failed", "no change"

    try:
        match = re.search(r'\{[\s\S]+\}', raw)
        if not match:
            # Fallback: if the LLM just returned the new asset directly (no JSON wrapper),
            # treat the entire output as the new asset
            if len(raw) > 50 and raw != current_asset:
                return raw.strip(), "direct output (no JSON)", "full rewrite"
            return current_asset, "parse failed", "no change"
        data = json.loads(match.group())
        new_asset = data.get("new_asset", current_asset)
        if not new_asset or len(new_asset) < 20:
            return current_asset, "empty mutation", "no change"
        return (
            new_asset,
            data.get("hypothesis", "no hypothesis"),
            data.get("diff_description", "no description"),
        )
    except (json.JSONDecodeError, KeyError) as e:
        # JSON parse failed — try extracting new_asset with regex instead
        # This handles cases where markdown content breaks JSON
        asset_match = re.search(r'"new_asset"\s*:\s*"([\s\S]+?)"\s*[,}]', raw)
        if asset_match:
            new_asset = asset_match.group(1).replace('\\n', '\n').replace('\\"', '"')
            hyp_match = re.search(r'"hypothesis"\s*:\s*"([^"]+)"', raw)
            diff_match = re.search(r'"diff_description"\s*:\s*"([^"]+)"', raw)
            return (
                new_asset,
                hyp_match.group(1) if hyp_match else "recovered from broken JSON",
                diff_match.group(1) if diff_match else "recovered",
            )
        # Last resort: if the raw output looks like a complete asset (has headings, is long),
        # use it directly
        if len(raw) > 200 and ('#' in raw or '##' in raw):
            # Strip any JSON wrapper attempts
            cleaned = re.sub(r'^[\s\S]*?"new_asset"\s*:\s*"?', '', raw)
            cleaned = re.sub(r'"?\s*\}\s*$', '', cleaned)
            if len(cleaned) > 100:
                return cleaned.strip(), "recovered from malformed output", "partial recovery"
        log.warning("Mutation parse error: %s", e)
        return current_asset, f"parse error: {e}", "no change"


# ---------------------------------------------------------------------------
# AutoResearch Loop
# ---------------------------------------------------------------------------

class AutoResearchLoop:
    """Run iterative optimization on a text asset.

    The loop:
    1. Evaluate current asset with eval_fn
    2. Mutate asset based on feedback
    3. Evaluate new asset
    4. A/B compare new vs current
    5. Keep winner, discard loser
    6. Repeat

    Args:
        name: Experiment name (for logging and history)
        asset_path: Where the asset lives on disk (or None for in-memory)
        eval_fn: fn(asset_text) -> str  (produces output to evaluate)
        criteria: {criterion_name: description} for judging
        directive: What to optimize for (instruction to mutator)
        mutate_model_fn: LLM for mutation (should be capable — Opus/GPT-5)
        judge_model_fn: LLM for judging (MUST differ from generator to avoid bias)
        history_dir: Where to store experiment history
    """

    def __init__(
        self,
        name: str,
        eval_fn: Callable[[str], str],
        criteria: dict[str, str],
        directive: str,
        asset_path: Optional[Path] = None,
        initial_asset: Optional[str] = None,
        mutate_model_fn: Callable = None,
        judge_model_fn: Callable = None,
        history_dir: Optional[Path] = None,
        rubric: str = "",
    ):
        self.name = name
        self.eval_fn = eval_fn
        self.criteria = criteria
        self.directive = directive
        self.rubric = rubric

        # Asset: load from file or use initial_asset
        if asset_path and asset_path.exists():
            self.asset_path = asset_path
            self.current_asset = asset_path.read_text(encoding="utf-8")
        elif initial_asset:
            self.asset_path = asset_path
            self.current_asset = initial_asset
        else:
            raise ValueError("Must provide asset_path (existing file) or initial_asset")

        # Model functions — default to cross-model setup
        if not mutate_model_fn:
            from sub_agent import claude_think
            mutate_model_fn = lambda p, t=180: claude_think(p, timeout=t, tier="heavy")
        if not judge_model_fn:
            # Use a DIFFERENT model for judging to avoid self-evaluation bias
            # Try GPT-5 first, fall back to Gemini — never use Claude (same as mutator)
            from sub_agent import model_think
            def _cross_model_judge(p, t=120):
                result = model_think(p, model_name="gpt5", timeout=t)
                if not result:
                    result = model_think(p, model_name="gemini", timeout=t)
                return result
            judge_model_fn = _cross_model_judge

        self.mutate_fn = mutate_model_fn
        self.judge_fn = judge_model_fn

        # History
        self.history_dir = history_dir or MIRA_ROOT / "agents" / "shared" / "autoresearch_runs"
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.experiments: list[Experiment] = []
        self.baseline_eval: Optional[EvalResult] = None

    def _history_summary(self, last_n: int = 5) -> str:
        """Summarize recent experiments for the mutator."""
        if not self.experiments:
            return ""
        recent = self.experiments[-last_n:]
        lines = []
        for exp in recent:
            kept = "KEPT" if exp.kept else "DISCARDED"
            score = exp.eval_result.aggregate if exp.eval_result else 0
            lines.append(
                f"- [{kept}] {exp.hypothesis} | score={score:.1f} | {exp.asset_diff}"
            )
        return "\n".join(lines)

    def _save_history(self):
        """Persist experiment history to disk."""
        path = self.history_dir / f"{self.name}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            if self.experiments:
                exp = self.experiments[-1]
                record = {
                    "iteration": exp.iteration,
                    "hypothesis": exp.hypothesis,
                    "asset_diff": exp.asset_diff,
                    "kept": exp.kept,
                    "timestamp": exp.timestamp,
                    "duration_s": exp.duration_s,
                    "eval_aggregate": exp.eval_result.aggregate if exp.eval_result else None,
                    "eval_scores": exp.eval_result.scores if exp.eval_result else None,
                    "compare_winner": exp.compare_result.winner if exp.compare_result else None,
                    "compare_confidence": exp.compare_result.confidence if exp.compare_result else None,
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _save_asset(self):
        """Write current best asset to disk."""
        if self.asset_path:
            self.asset_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.asset_path.with_suffix(".tmp")
            tmp.write_text(self.current_asset, encoding="utf-8")
            tmp.replace(self.asset_path)

    def _save_best(self):
        """Save the current best asset as a snapshot."""
        snap = self.history_dir / f"{self.name}_best.txt"
        snap.write_text(self.current_asset, encoding="utf-8")

    def run(
        self,
        max_iterations: int = 10,
        time_budget_minutes: float = 60,
        min_improvement: float = 0.3,
        save_every: bool = True,
    ) -> dict:
        """Run the optimization loop.

        Args:
            max_iterations: Max number of mutation-evaluate cycles
            time_budget_minutes: Stop after this many minutes
            min_improvement: Minimum score improvement to keep a mutation
            save_every: Write asset to disk after each improvement

        Returns:
            Summary dict with results
        """
        start_time = time.time()
        deadline = start_time + time_budget_minutes * 60

        log.info("AutoResearch [%s] starting: %d iterations, %.0f min budget",
                 self.name, max_iterations, time_budget_minutes)

        # Evaluate baseline
        log.info("[%s] Evaluating baseline...", self.name)
        baseline_output = self.eval_fn(self.current_asset)
        self.baseline_eval = llm_judge(
            baseline_output, self.criteria, self.rubric, self.judge_fn
        )
        best_score = self.baseline_eval.aggregate
        log.info("[%s] Baseline score: %.2f", self.name, best_score)

        improvements = 0
        for i in range(max_iterations):
            if time.time() > deadline:
                log.info("[%s] Time budget exhausted after %d iterations", self.name, i)
                break

            iter_start = time.time()
            log.info("[%s] Iteration %d/%d (best=%.2f)", self.name, i + 1, max_iterations, best_score)

            # Get feedback for mutator
            feedback = self.baseline_eval.reasoning
            if self.experiments and self.experiments[-1].eval_result:
                feedback = self.experiments[-1].eval_result.reasoning

            # Mutate
            new_asset, hypothesis, diff_desc = mutate_asset(
                self.current_asset, feedback, self._history_summary(),
                self.directive, self.mutate_fn
            )

            if new_asset == self.current_asset:
                log.info("[%s] Mutation produced no change, skipping", self.name)
                continue

            # Evaluate candidate
            candidate_output = self.eval_fn(new_asset)
            candidate_eval = llm_judge(
                candidate_output, self.criteria, self.rubric, self.judge_fn
            )

            # A/B compare
            compare = llm_compare(
                baseline_output, candidate_output, self.criteria, self.judge_fn
            )

            # Decision: keep if score improved, or comparator strongly prefers it
            # BUT never allow score to regress more than 0.5 even if comparator likes it
            score_improved = candidate_eval.aggregate > best_score + min_improvement
            comparator_picks = compare.winner == "b"  # b = candidate
            score_regression = best_score - candidate_eval.aggregate
            hard_guard = score_regression <= 0.5  # never accept large score drops
            keep = (score_improved or (comparator_picks and compare.confidence > 0.7)) and hard_guard

            exp = Experiment(
                iteration=i + 1,
                hypothesis=hypothesis,
                asset_diff=diff_desc,
                eval_result=candidate_eval,
                compare_result=compare,
                kept=keep,
                duration_s=round(time.time() - iter_start, 1),
            )
            self.experiments.append(exp)
            self._save_history()

            if keep:
                improvements += 1
                self.current_asset = new_asset
                baseline_output = candidate_output
                best_score = candidate_eval.aggregate
                log.info("[%s] IMPROVEMENT #%d: %.2f → %.2f (%s)",
                         self.name, improvements, best_score - candidate_eval.aggregate + best_score,
                         best_score, diff_desc)
                if save_every:
                    self._save_asset()
                    self._save_best()
            else:
                log.info("[%s] Discarded: score=%.2f, compare=%s (conf=%.2f) | %s",
                         self.name, candidate_eval.aggregate, compare.winner,
                         compare.confidence, hypothesis)

        elapsed = time.time() - start_time

        summary = {
            "name": self.name,
            "iterations": len(self.experiments),
            "improvements": improvements,
            "baseline_score": self.baseline_eval.aggregate,
            "final_score": best_score,
            "score_delta": round(best_score - self.baseline_eval.aggregate, 2),
            "elapsed_minutes": round(elapsed / 60, 1),
            "experiments": [
                {
                    "i": e.iteration,
                    "hypothesis": e.hypothesis,
                    "score": e.eval_result.aggregate if e.eval_result else 0,
                    "kept": e.kept,
                }
                for e in self.experiments
            ],
        }

        # Save final summary
        summary_path = self.history_dir / f"{self.name}_summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        log.info(
            "AutoResearch [%s] complete: %d iterations, %d improvements, "
            "%.2f → %.2f (+%.2f) in %.1f min",
            self.name, len(self.experiments), improvements,
            self.baseline_eval.aggregate, best_score,
            best_score - self.baseline_eval.aggregate, elapsed / 60
        )

        return summary


# ---------------------------------------------------------------------------
# Convenience: batch optimize multiple assets
# ---------------------------------------------------------------------------

def batch_optimize(
    configs: list[dict],
    time_budget_minutes: float = 120,
) -> list[dict]:
    """Run autoresearch on multiple assets sequentially, splitting time budget.

    Each config dict needs: name, eval_fn, criteria, directive, asset_path or initial_asset.
    Optional: mutate_model_fn, judge_model_fn, rubric, max_iterations.
    """
    per_asset = time_budget_minutes / max(len(configs), 1)
    results = []
    for cfg in configs:
        loop = AutoResearchLoop(
            name=cfg["name"],
            eval_fn=cfg["eval_fn"],
            criteria=cfg["criteria"],
            directive=cfg["directive"],
            asset_path=cfg.get("asset_path"),
            initial_asset=cfg.get("initial_asset"),
            mutate_model_fn=cfg.get("mutate_model_fn"),
            judge_model_fn=cfg.get("judge_model_fn"),
            rubric=cfg.get("rubric", ""),
        )
        result = loop.run(
            max_iterations=cfg.get("max_iterations", 10),
            time_budget_minutes=per_asset,
        )
        results.append(result)
    return results
