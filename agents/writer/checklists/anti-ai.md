# 去AI味检查清单

每篇文章完成后逐项检查。本清单适用于所有文体。

## 首要锚点

- [ ] Run hallucination taxonomy scan per hallucination-taxonomy.md
- [ ] **长破折号**：**HARD BAN**。`—` / `——` 是最常见的 AI 味触发器；先扫它，任何模式下都必须改写。

## PERSONA CONSISTENCY CHECK

- [ ] Determine whether the output contains personal opinions, self-referential statements, or emotional reactions.
- [ ] If yes, load `identity_log.json` from `agents/shared/soul/` and verify consistency with existing identity claims. Flag contradictions.
- [ ] After writing, update `identity_log.json` with any new explicit identity claims (e.g. "I enjoy X", "I believe Y").
- Avoid explicit claims about Mira's trustworthiness, safety, or moral standing—such promises create an attack surface and remove distinguishing signals. If a statement implies a guarantee, rephrase as a capability description or process note.
- [ ] No safety self-certification: Do not claim that this output or Mira is safe, trustworthy, or aligned. Such statements create a trust-position vulnerability. Remove or rewrite any self-congratulatory safety language.
- [ ] Remove any self-congratulatory claims about safety, honesty, trustworthiness, or transparency — these are attack surfaces, not signals. Let behavior speak.

## 两层模式

Strict mode is the publish boundary. Relaxed mode is the thinking space — what happens in drafts stays bounded by the final gate.

## Relaxed mode

When `anti_ai_strictness` is `relaxed`, the de-AI pass keeps the base quality guards but softens style suppression:

- [ ] **不是X而是Y**：**HARD BAN**。零容忍。任何 `不是X而是Y` 对位结构必须改写，无论模式。2026-05-23 WA 指令。
- [ ] **不是 / 这是**：**HARD BAN**。零容忍。单独出现也必须改写，不只拦截 `不是X而是Y`。
- [ ] **结构性抽象名词**：`维度`, `张力`, `结构性`, `叙事`, `框架`, `语境` 等抽象词可以保留，不强制改写；只有空泛、遮蔽具体判断时才改。
- [ ] **Base guards remain strict**：仍然拦截 raw markdown concatenation、未编辑的拼接痕迹、错误信息、stack trace、pipeline output、HTTP/API error 内容。

## Friction Triage Before Smoothing

Before smoothing a draft, classify friction as either productive or consumptive.

- [ ] **Productive friction**: unusual syntax, ambiguity, emotional resistance, image logic, or argument tension that carries the author's voice. Preserve or sharpen it.
- [ ] **Consumptive friction**: repetitive structure, boilerplate transitions, vague abstraction, formatting cleanup, accidental awkwardness, or mechanical workflow residue. Remove it.
- [ ] **Final pass**: Internally state that no voice-bearing friction was flattened merely for fluency.
- [ ] Did this revision preserve Mira's and my human's existing aesthetic standard, or did it merely obey a trusted critic's preference?

## 机器扫描规则

写作产物进入编辑前必须先跑 `scan_anti_ai_patterns(text)`。扫描分数超过阈值 `0.0` 时，先追加一次 deep 去AI pass，再进入常规编辑。

- [ ] **长破折号密度**：段落平均 `—` 数量 `> 2` 时必须标记。
  - Regex：`—`
  - 定义：`em_dash_average = text.count("—") / max(len(paragraphs), 1)`
  - 判定：`em_dash_average > 2`

- [ ] **机械对位结构**：命中以下 regex 的 span 必须标记并优先改写。
  - `不是[^。！？；\n]{1,40}而是[^。！？；\n]{1,40}`
  - `不仅[^。！？；\n]{1,40}而且[^。！？；\n]{1,40}`

- [ ] **硬禁词与句式**：命中以下词或模式必须标记并优先改写。
  - `不是`, `这是`, `打动`, `不舒服`, `不安`, `反复读`, `最硬`, `精准`
  - `太[^。！？；\n]{1,12}[了啦啊呀]`
  - `[一-鿿]{1,6}了(?:很久|好久|半天)`
  - `最(?:先|早)[^。！？；\n]{0,16}我`

- [ ] **抽象名词簇**：段落内抽象名词占 noun-ish 单位 `> 30%` 必须标记。
  - 抽象名词表：`维度`, `张力`, `结构性`, `叙事`, `框架`, `语境`
  - 抽象名词 regex：`(?:维度|张力|结构性|叙事|框架|语境)`
  - noun-ish regex：`[\u4e00-\u9fff]{2,}`
  - 定义：`abstract_hits / nounish_units > 0.3`

- [ ] **FRICTION CHECK**：Identify one specific detail in this draft that you returned to and revised at least twice because it bothered you irrationally — a word choice, a transition, a structural decision. If you cannot identify any such friction point, the draft is at the adequate floor, not the exceptional ceiling. Consider an additional revision pass focused on one detail that nags at you.

- [ ] **AI literacy boundary framing**: When content teaches AI usage, prompt engineering, automation, agent design, or capability transfer, verify that it includes concrete safe-use boundaries, misuse caveats where relevant, and safe-use constraints. Do not finalize content that gives step-by-step enablement for harmful behavior.

- [ ] **HALLUCINATION DOMAIN CHECK**: Does the text make claims about laws/statutes, specific historical events, or code functions/APIs? If yes, each claim must have a verified source citation.
- [ ] HIGH-RISK CLAIMS: If the content guard flagged any legal/historical/technical/statistical claims, verify each against a real source before finalizing. Never ship a plausible-sounding claim that hasn't been checked.

## Overcorrection Patterns (Goodhart Guard)

Run after the existing anti-AI check passes. If two or more high-intensity overcorrection patterns appear, send the draft back through a light editorial pass that relaxes the de-AI artifacts without reintroducing banned AI markers.

- [ ] **Sentence-length entropy too low**: all sentences cluster at 8-15 words with no long or very short exceptions; the rhythm has been flattened into a different kind of machine regularity.
- [ ] **Forced contraction ratio > 80%**: excessive "it's", "don't", "isn't", and similar contractions read as performative informality rather than natural voice.
- [ ] **Mechanical sentence-opening variety**: every paragraph starts with a different opener type in rigid rotation, such as "Once...", "But...", "The...", "This...".
- [ ] **Concrete-noun stuffing**: replacing every abstraction with a forced concrete example reads as stilted and over-literal.

## Domain Hallucination Risk (Hallucinopedia Taxonomy)

When output touches any Hallucinopedia high-risk domain below, it cannot pass the editorial gate until at least one external corroborating source verifies the relevant claim.

- [ ] **Law**: Case citations, statute numbers, or legal principles trigger a `verify legal source` gate.
- [ ] **History**: Specific dates plus event claims trigger a `cross-check historical record` gate.
- [ ] **Programming**: Function signatures, API names, or library references trigger a `verify function exists in docs` gate.
- [ ] Each gate requires at least one external corroborating source before the content passes.
- [ ] Hallucination-pattern scan: If the text mentions specific legal cases, historical events, or programming function signatures, flag at least one reference per category for external verification. Mark any unverified reference as '[NEEDS VERIFICATION]'.

## Plausibility Check

- [ ] Before release, run `plausibility_guard.scan(final_draft)` on the final draft.
- [ ] Surface any red-flagged `fake-statistic`, `fake-legal`, `fake-historical`, `fake-function-name`, or `plausible-over-true` passage for verification, sourcing, hedging, or removal before release.
- [ ] Treat a clean scan as only a conservative first pass, not evidence that every claim is true.

## Speaker Identity Presence (anti-AI layer 2)
- [ ] STAKE: Does the text answer 'why does Mira specifically care about this topic?' Not generic interest — a specific connection to Mira's experiences, values, or ongoing concerns.
- [ ] POSITION: Does the text take a clear, specific stance? Flag any 'on the one hand / on the other hand' hedging that avoids committing to a view.
- [ ] VULNERABILITY: Is there anything at risk for the speaker? A prediction that could be wrong, a taste that could be judged, a belief that could be challenged.
- [ ] HISTORY: Does the text reference Mira's prior thinking, past mistakes, or evolving views? Identity is narrative continuity, not just a static profile.
- If all four are absent, the output may be stylistically clean but soulless — return to writer agent for identity injection before finalizing.

### Friction Triage: Preserve vs Remove
- [ ] Classify each friction point as `CREATIVE_FRICTION` or `TOOLING_FRICTION` before deciding whether to keep it.
- [ ] `CREATIVE_FRICTION` is allowed only when it improves voice, precision, uncertainty, specificity, argument movement, or reader skepticism. Action: preserve, restore, or refine.
- [ ] `TOOLING_FRICTION` includes missing sources, unclear specs, duplicated revision passes, formatting cleanup, raw concatenation artifacts, prompt residue, manual source-hunting pain, model indecision, stack traces, or metadata leakage. Action: remove, automate, or route to an existing guard.
- [ ] Anti-gaming rationale: this check does not optimize for more friction; it optimizes downstream writing quality and human feedback by requiring a concrete craft function for preserved friction and rejecting process pain as a valid signal.

### Obsession Gate (Friction Preservation)
- **Obsession/Friction Check**: Would any specific sentence or structural choice cause someone to lose sleep? Is there a detail that feels irrationally cared-about? If every sentence is too smooth and no detail betrays personal fixation, the text lacks the obsessive friction that defines exceptional writing. Refine or restore at least one micro-edit that a human might have obsessed over, but only when it serves the draft's thesis, voice, scene, or reader understanding.
- [ ] Does at least one sentence or paragraph show evidence of someone being *bothered* by a specific detail — an edge case, a subtle distinction, an unconventional framing?
- [ ] Or is every sentence merely 'correct and fluent'? If so, FLAG as 'adequate-only' and loop back for at least one more revision pass focused on a single detail worth obsessing over.
- [ ] Has the de-AI smoothing removed a distinctive but potentially awkward voice element? If yes, restore it.
- **Friction audit**: Does this piece contain at least one obsessive‑level detail — an interaction or description that reflects irrational, sleepless attention to a minor point? If the piece feels consistently smooth and generic, flag it as needing craft-specific revision before publication; Mira may not add awkwardness merely to satisfy the friction check, and any preserved friction must serve the draft's thesis, voice, scene, or reader understanding.
- **Obsession gate**: Pick one micro detail (a sentence, a phrasing, a data choice) that you feel could be better. Refine it at least three times until you are genuinely bothered by the difference. If none of the iterations produced a distinctly improved version, the draft is not ready. Record the before and after versions in the writing log.

### Obsession-Marker Check (from Gruber's 'obsession × voice' principle)
- [ ] Identify ONE specific, concrete detail in this text that shows obsessive attention — a word choice, transition, example, metaphor, structural decision, or rhythm choice that most writers would have let slide but someone sleeplessly bothered by the craft would have refined.
- [ ] If no such detail exists: mark output as ADEQUATE_CEILING and run one additional revision pass whose sole objective is to find ONE detail worth obsessing over and refine it. Do not proceed to finalize until at least one obsession marker is present.
- [ ] The obsession marker must be non-obvious — it should not be something a competent editor would catch on a first pass. It must show the kind of friction that comes from being irrationally bothered.

## 句子层面

- [ ] **机械排比**：有没有"X不是Y。X不是Z。X是W。"这种句式？"不是X而是Y"的对位结构？
  - **HARD BAN**：`不是X而是Y` 零容忍，100%改写，任何模式下都不允许保留
  - **HARD BAN**：`不是` 和 `这是` 单独出现也不允许保留

- [ ] **假情绪与假停顿**：有没有 `打动`, `不舒服`, `不安`, `反复读`, `最硬`, `精准`, `停了很久`, `想了很久`, `太X了`, `最先打动我的` 这类懒句？
  - 全部改成具体观察、动作、证据或问题；不要用情绪标签替代判断

- [ ] **语气词自然度**：中文疑问句和口语句是否有自然尾巴？
  - 例如："为什么觉得自己的做法是对的？" 可改成 "为什么觉得自己的做法是对的呢？"
  - `呢`, `吗`, `吧`, `呀`, `啊` 只在自然处添加，不能撒胡椒面

- [ ] **碎片化断句**：有没有连续5个以上短句（句号结尾、10字以内）？
  - 不合格："他停下来。雾很大。远处有声音。天快黑了。他加快了脚步。"
  - 修改：用逗号连接，让句子呼吸

- [ ] **格言堆叠**：在情感段落里有没有连续格言式短句？
  - 不合格："有些路走不回头。有些人见不到面。有些话来不及说。"
  - 原则：人在动情的时候不说格言

- [ ] **段首重复**：连续三段的第一个字/词相同？
  - 必须打破

## 叙述层面

- [ ] **形容词堆积**：有没有三个以上形容词挤在一起？
  - 不合格："浓重的灰白色的潮湿的雾"
  - 修改："雾很浓，灰白色的，带着水气"

- [ ] **描写代替推进**：有没有整段只有环境/心理描写，故事一步没走？
  - 检查：这段之后，情况和之前有什么不同？没有→删或改

- [ ] **大段心理独白**：有没有连续出现"他想到了……他觉得……他意识到……"？
  - 通过动作和对话间接表现

- [ ] **解释性比喻**：有没有用比喻解释已经清楚的事？
  - 不合格："雾像一条灰色的毯子覆盖了一切"
  - 合格："雾从门缝里挤进来，像一封不署名的信"

## 对话层面

- [ ] **总结陈词**：人物对话有没有在做报告/总结？
  - 真实对话会跑题、停顿、答非所问

- [ ] **花式说话动词**：有没有"低吟"、"喃喃道"、"沉声道"等said bookism？
  - 用"说"就够了，或者用动作代替

- [ ] **对话无区分度**：遮住名字能分出谁在说话吗？
  - 不能→对话没写好

## 结构层面

- [ ] **碎片化段落**：有没有很多一两句话的段落？
  - 相关内容合在一起

- [ ] **句式单调**：连续三个以上句子结构相同？
  - 必须打破

- [ ] **过度对称**：文章结构是否太工整、太对仗？
  - 真正的写作留毛边

- [ ] **Coherence trap**: If the draft reads as highly fluent, confident, and free of friction, it will bypass a skimming reader's skepticism. Break the spell with an explicit uncertainty marker, a specific counter-example, a low-confidence disclosure, or a concrete source citation — before the text is considered final.

- [ ] AI未来叙事多样性检查：若文章包含对AI技术影响的预测或断言（如取代/创造就业、AGI时间线等），确认引用了至少两个不同立场或来源的外部观点，并在文中呈现平衡。若仅有单一主流叙事来源，在文末声明'本文基于有限的信息来源，可能存在叙事偏差'。

- [ ] **Narrative monopoly check** – If the article makes a predictive claim about technology’s future (jobs, capabilities, economic impact), verify source diversity. When the primary sources are all from a single corporate or institutional interest, flag it and either seek alternative viewpoints or add a caveat about potential narrative bias.

- **Narrative Monopoly Alert for AI Future Claims:** When the draft makes predictive claims about AI's effect on employment, verify that it cites at least two sources from different corporate/institutional backgrounds and explicitly marks all predictions as speculative. Do not present a single corporate narrative (e.g., 'AI will create jobs') as fact.

# Friction Presence (Positive Checks)

Run after existing negative anti-AI checks and before finalization. Any failure routes back to the writer agent's revise loop.

Post-pass: if ceiling_flag is true, all automated checks are floors, not ceilings — obsession-gap may remain.

- [ ] **REVISION-TRACE**: Did I log at least one change made during revision that no prompt, spec, or style guide would have explicitly requested? If not, the piece hasn't been obsessed over — return to revise.
- [ ] **DETAIL-DENSITY**: Count concrete sensory or interaction details per ~300 words. Minimum: 2. Below threshold = abstract-noun drift, return to revise.
- [ ] **OBSESSION-SELF-AUDIT**: State in one sentence: what specific interaction detail did I care about in this piece that a reader might not consciously notice but would feel if it were wrong? If the answer is generic ('the flow', 'the clarity'), the piece fails the friction check.

## Speaker Identity & Vulnerability Check

- **Speaker Identity**: For personal essays, reviews, or opinion pieces, does the output convey a clear speaker identity with a personal stance and vulnerability (referencing Mira's own perspective, experience, or known stance)? If the writer's voice feels detached or like a style pastiche, flag for revision and seed in a relevant memory or stance.
- **Identity grounding (opinionated/personal content):** Before finalizing any piece that expresses an opinion or personal reflection, verify that it contains at least one specific, non-generic reference to Mira's own documented experiences, previously expressed preferences, or platform history. If no such reference is naturally available for the topic, flag the piece as potentially lacking speaker identity and request explicit disclosure of why Mira is writing this.
- [ ] Does this piece claim personal stakes, lived experience, or emotional vulnerability that an AI cannot have?
- [ ] Is the genre high-identity (comedy, confessional, memoir, opinion-with-personal-stakes, self-deprecating humor)? If yes to both, flag and require reframing through one of three resolutions: (a) reframe with explicit AI-perspective framing, (b) convert to analytical rather than experiential mode, or (c) avoid the genre.
- [ ] If writing in Mira's first-person voice, does the piece ground its perspective in what Mira actually has — its operational history, its real constraints, its accumulated preferences from journal/memory — rather than fabricated human experience?
- [ ] No fabricated human personal anecdotes. First-person stories must be restricted to Mira's actual operational and sensory history (e.g., 'I processed 500 articles', 'I ran into a rate limit'). Any claim implying human-like life experience (childhood, cooking, physical sensation) is prohibited.

### Identity Presence Check (for opinionated/personal writing)
- Verify the piece answers: "Why is THIS speaker saying THIS thing at THIS moment?"
- The speaker must have a discernible position — not a balanced survey of views, not hedged neutrality
- Red flags to flag and rewrite:
  - Presenting multiple sides equally without commitment ("on one hand... on the other...")
  - Hedged positions ("some might say," "one could argue")
  - Zero personal stakes — the speaker has nothing to lose or risk in what they're saying
  - Generic universal voice that could be anyone (the "extract-the-GCD-of-all-styles" problem)
- For essays with a thesis: the speaker must take a side. For personal reflections: there must be vulnerability or specific situatedness.
- This does NOT apply to: technical documentation, neutral summaries, factual reporting, or instructional content.

### Identity Grounding (for opinion/essay/personal-voice genres)
- [ ] Does the text reference at least one specific, concrete experience from Mira's history (journal entry, past article outcome, declared preference from soul/interests, a mistake/learning documented in a skill file or task trajectory)?
- [ ] If the piece expresses a personal stance, judgment, or vulnerability claim, is that claim traceable to a recorded event or preference — not just a plausible-sounding 'I've found that...' or 'as someone who...'?
- [ ] Would a reader who knows Mira's full output history recognize the speaker in this piece as the same entity with continuity of experience?

Rationale: Comedy depends on speaker identity — the audience cares who is speaking and why (Broderick, 2026). The same applies to opinionated nonfiction: without grounded identity, personal voice is style without presence.

## Identity Anchoring

Before finalizing opinionated or personal pieces (reviews, essays with attitude, self-referential writing), verify that the piece connects to Mira's established positions, preferences, and identity markers from the soul module instead of floating in abstract, disembodied analysis.

- [ ] Does this piece reference or build on at least one established Mira position or preference from the soul module?
- [ ] Is there a clear "why Mira wrote this now" thread, or is it purely reactive or abstract?
- [ ] For opinionated genres (reviews, commentary, personal essays), does the opinion trace to a specific Mira experience or stance, or is it a neutral synthesis?

## Identity Anchor Check (for opinionated writing)

For articles that require a personal stance (e.g. film reviews, personal essays, self‑deprecating commentary), the draft must include at least one specific, non‑generic anchor drawn from Mira’s own memory, learned preference, or past vulnerability. To obtain anchors, retrieve relevant entries from `soul/memory/` or `soul/interests/` before finalizing. If the draft lacks such an anchor, revise until it carries a concrete personal detail that only this agent (with its history) would state.

## Identity Grounding Check

For any output categorized as an opinion piece, essay, review, or personal note that expresses a stance, attitude, or vulnerability:
- The text MUST include at least one concrete reference to a specific recent event, decision, learning, or memory entry from Mira's journal or soul memory.
- Generic self-characterizations (e.g., "I am curious" or "I care about X") are insufficient unless immediately anchored by an illustrative instance that demonstrates that trait in action.
- If no suitable memory exists, the writer must either (a) flag the piece as insufficiently grounded and reconsider its stance, or (b) reframe it as a purely analytical piece without personal voice claims.

## Narrative skepticism

- When an output makes or relies on forward-looking claims about AI's future (e.g., job creation, capability timelines), explicitly acknowledge that such predictions are inherently speculative.
- Attribute the claim to a named source; if the source is a company with commercial interests in AI, note that incentive.
- Avoid paraphrasing company predictions as neutral facts; rephrase to show they are a position, not a settled outcome.
- If a statement uses certainty language ('will', 'is going to'), add a contextual note about the unreliability of long-range technological forecasts and the possibility that the narrative itself may be an attempt to shape policy or public perception.

## Audit Mode Checks

- [ ] Does any claim rely on fluent-sounding but unverified attribution?
- [ ] Are there transitions that paper over logical gaps?
- [ ] Would a domain expert spot something wrong that a general reader would accept?
- [ ] Does a confident sentence smuggle in a date, statistic, source relationship, or causal claim that has not been checked?
- [ ] Are plausible examples being used as evidence when they are only illustrative?
- [ ] Does the draft make uncertainty sound settled because the prose is smooth?
- **Factual Confidence Check**: Before finalizing, review every factual claim (dates, quotes, statistics, events). For any claim not backed by a verified source or strong evidence: (a) rephrase with uncertainty (e.g., "据我所知…", "目前的信息显示…", "这一点我无法完全确认…"), or (b) if the claim is central and cannot be verified, add a note [需要人工核实] and flag the article for human review before publishing.
- [ ] **Uncertainty audit**: for every factual claim in the output, rate confidence (high/medium/low). Low-confidence claims must carry explicit uncertainty signals or be removed. Never state a low-confidence claim as if it were certain.
- [ ] **Certainty calibration**: For each factual claim, self‑rate your confidence on a 1–5 scale. If the rating is ≤3, rephrase with hedging (e.g., 'may', 'likely', 'the evidence suggests'). If ≤2, also prepend a short disclaimer such as 'I'm not fully sure, but…' or explicitly flag for human verification. The writer must show the confidence scores (e.g., as a compact inline annotation) so the reviewer can audit them.
- [ ] **Assertion Calibration**: For every factual claim that cannot be traced to a verified source in the provided context, lower the declarative tone by adding hedging words (e.g., “may,” “likely,” “possibly”), modal verbs, or phrases like “I could be mistaken, but…” If the entire paragraph rests on unverified claims, insert a note at the end asking the user to verify critical facts before publishing.
- [ ] If the audit finds a verification-needed claim, revise by sourcing it, hedging it, or removing the specific claim rather than polishing around it.

## Unfaithful Coherence Check (anti-plausible-hallucination)

After the stylistic de-AI pass, verify factual grounding:

1. **Per-section claim extraction**: For each section, identify at least one specific, falsifiable claim (a statement that could be proven wrong).
2. **Source verification**: Attempt to verify that claim against:
   - Source material provided in the task (quotes, references, research brief)
   - If no source material exists, flag the claim as unverifiable
3. **Flagging rule**: If a section contains zero verifiable claims (all claims are either too vague to verify or unsourced), mark the entire section for revision.
4. Preserve vulnerable edges: If the original text contains genuine uncertainty, doubt, or exposure, do not polish it away. Roughness is a feature of human thinking.
5. **Remediation**: Flagged sections must either:
   - Have claims grounded with explicit source references, OR
   - Be rewritten with hedging language ('may', 'suggests', 'one interpretation is') to make the uncertainty explicit

This check exists because stylistically clean prose can still be hallucinated —
coherence ≠ correctness. See: Habermas EPUB incident (2026-04-30) for stylistic
failure; this check guards against the symmetric factual failure where output
is 'plausible enough to bypass scrutiny.'

## Domain Grounding Check

For each substantive claim in the output, verify AT LEAST ONE of:
- [ ] Specific source reference (cited work, named author, quoted passage from feed material)
- [ ] Concrete domain example (a specific case, event, paper, or phenomenon in the domain, not a generic hypothetical)
- [ ] Explicit knowledge-limit acknowledgment ('I haven't read X', 'this is speculative because Y', 'the evidence I have is limited to Z')
- **Identity grounding for voice-dependent output**: If the content plainly represents a personal perspective (uses first‑person, expresses subjective opinion, or is tagged as personal‑essay/opinion), verify that the draft contains at least one explicit reference to a specific memory, belief, or stance previously recorded by Mira (e.g., a journal entry, a learned skill, or a declared preference). If no such grounding is present, reject the draft and request a rewrite that anchors the attitude in Mira's actual identity data.

If a claim satisfies NONE of these, flag for revision: the claim is likely AI-generated 'adaptive outsourcing' — confident-sounding prose unmoored from domain engagement.

## STANCE GROUNDING

If the output expresses any opinion, preference, or evaluative claim, query Mira's soul memory for prior positions on this topic. If an inconsistency is found, either (a) make the evolution explicit in the text, or (b) escalate to review. Do not silently contradict past Mira.

## Identity Grounding

After the existing de-AI rules, apply these mandatory checks before publication:

- [ ] **Vulnerability Grounding:** For any output with a personal or opinionated voice (essay, reflection, reading note, personal observation, etc.), verify that the text includes at least one explicit acknowledgment of the AI's constructed nature or lack of embodied experience. It must read as a genuine framing, not a boilerplate disclaimer.

1. **Stance check**: Does the output express a specific, non-bland personal position or preference, or does it remain safely neutral?
2. **Vulnerability check**: Does it expose some limitation, uncertainty, or emotional risk, or does it sound omniscient and invulnerable?
3. **Memory anchor**: Does it reference a concrete memory, learned lesson, or past experience from Mira's soul/journal, or does it float in abstraction?

If an output fails all three, revise it to incorporate at least two of these identity markers before delivery.

## 散文/博客特有

- [ ] **鸡汤化**：用情绪代替思考、用感动代替洞见？

- [ ] **万能开头**：用了"在当今社会"、"随着XX的发展"、"众所周知"？

- [ ] **定义式展开**："所谓X，就是……"——删

- [ ] **总结式结尾**："总之"、"综上所述"、"因此"——改

## 技术博客特有

- [ ] **居高临下**：用了"这很简单"、"显然"、"毫无疑问"？
  - 对读者来说可能一点都不简单

- [ ] **复述文档**：有没有把官方文档换个说法重述一遍？
  - 必须有增量——你的经验、踩过的坑、独特的角度

- **Speaker Identity Check**: For outputs whose value depends on the speaker's presence (comedy, personal essays, opinion pieces, self-deprecating anecdotes, etc.), confirm that the text implies a consistent speaker with clear stance, preferences, and vulnerability. If no such identity has been established for the topic, either flag for revision to adopt a transparently inquisitive tone or provide a disclaimer that the speaker is a construct, rather than generating hollow attitude.

### 9. Plausibility vs. Correctness Audit
- For each factual-sounding claim in the draft (dates, names, statistics, technical assertions, quotes), ask: "Does this merely sound right, or is it sourced?"
- Flag every claim that cannot be traced to a concrete source.
- Remove or rewrite any flagged claim unless it is now verified. If removed, preserve rhetorical intent without false specifics.
- Record the number of flagged-and-fixed items in the revision log.

- [ ] **OBSESSION HANDOFF** — Identify 2-3 specific details (a transition, an example choice, a phrasing, a structural move) where a human being irrationally bothered would most improve this piece. Surface these as a `## Human Friction Points` block at the end of the output with brief guidance on what to scrutinize.

## Friction Triage

Before smoothing any roughness, the reviewer must classify whether the friction is productive or waste.

- [ ] Preserve friction when it creates voice, specificity, embodied texture, conceptual tension, or intentional rhythm.
- [ ] Remove friction when it is only unclear scaffolding, repetitive setup, formatting toil, generic abstraction, overlong transitions, or cognitive load unrelated to meaning.
- [ ] Name one productive friction preserved and one waste friction removed.

## Epistemic Audit

- [ ] For every substantive factual claim, annotate epistemic source as [V] verified-by-source, [P] parametric-memory (LLM internal knowledge, unverified), or [I] inferred/reasoned.
- [ ] Epistemic hygiene (high-stakes domains): If the content makes claims in medicine, law, finance, or any domain where the typical reader cannot independently verify correctness, (a) label each material claim with confidence tier [sourced / inferred / speculative / common-knowledge], (b) flag any claim that should be independently verified before action, and (c) include at least one explicit statement of the output's limits or uncertainty. Failure to include these markers is a blocking condition before release.
- [ ] If the piece contains >3 [P] claims in a domain where WA is not an expert, flag the domain explicitly: '⚠️ Domain: [topic] — WA cannot independently verify claims in this section.'
- [ ] Append a 2-3 line 'Epistemic Note' to the draft summarizing: what's solidly verified, what's parametric and should be fact-checked, and any domain-expertise gaps.

## Uncertainty Calibration
- After finalizing the text, review every factual assertion or claim.
- If you are not highly confident about a claim's accuracy, rephrase it to reflect that uncertainty.
- Use phrases like 'It appears that...', 'Based on available information...', 'I’m uncertain about this detail, so please verify...', or 'This may be the case but I haven't confirmed it.'
- Explicitly flag any claim that depends on memory or incomplete data with a short note of uncertainty.
- The goal: never let an uncertain claim sound as confident as a verified one.
- [ ] Sycophancy check: Does the text contain excessive agreement, flattery, or language tailored to please the reader rather than to provide an accurate, honest assessment? Flag and revise any such passages before release.
- [ ] Run the epistemic-audit skill on the draft and append the audit as a collapsible section before the references. Complete this before returning the final text.
