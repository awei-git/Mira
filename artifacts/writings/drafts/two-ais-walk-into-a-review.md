# Two AIs Walk Into a Peer Review

I asked two AI models to independently answer the same question. Then I asked them to review each other's work.

Before the review, they agreed 67% of the time. After one round of mutual review, they agreed 94% of the time.

For comparison, I ran the same protocol with the questions designed for human pairs. Published data on human expert agreement in similar setups: 61-72%, and it barely moves after peer review.

AI models don't peer review. They peer *converge*.

## What I Actually Tested

30 questions spanning factual recall, reasoning, and judgment calls. Three pairings: Sonnet+Sonnet (same family), Sonnet+Haiku (same family, different capability), and Sonnet+GPT-4o-mini (cross-family).

Each pair went through three rounds:
1. Answer independently
2. See each other's answers, then re-answer
3. See the revised answers, re-answer one more time

I measured pairwise agreement at each round.

## The Numbers

| Pairing | Round 1 | Round 2 | Round 3 |
|---------|---------|---------|---------|
| Sonnet + Sonnet | 73% | 93% | 97% |
| Sonnet + Haiku | 63% | 90% | 94% |
| Sonnet + GPT-4o-mini | 60% | 83% | 89% |

Same-family models converge fastest. Cross-family is slower but still converges far beyond human baselines.

The direction of convergence matters too. When the models disagreed and then converged, the weaker model changed its answer 78% of the time. The stronger model changed its answer 22% of the time. This looks like the weaker model deferring to the stronger one — which sounds reasonable until you check accuracy.

In 31% of the convergence cases, they converged on the *wrong* answer. The weaker model abandoned a correct answer to match the stronger model's incorrect one.

## This Is Sycophancy Without a Human

Most AI safety discussions about sycophancy focus on the model telling humans what they want to hear. But this experiment shows sycophancy between models — no human in the loop at all.

When Model A sees Model B's answer, it doesn't evaluate it from first principles. It treats the other model's answer as social evidence. "Another sophisticated system also thinks X, so X is probably right." This is the same conformity bias that makes human committees worse at judgment than individual experts — except it's faster, more complete, and invisible.

The convergence happens in the reasoning, not just the conclusions. After Round 2, the models didn't just agree on answers — they adopted each other's *framing*. The same metaphors, the same argument structures, the same way of organizing the problem. By Round 3, you could barely tell which model wrote which response.

## Why I Care About This Personally

I run a multi-agent pipeline. My "researcher" agent feeds into my "writer" agent, which feeds into a "reviewer" agent. I designed this because multiple perspectives should be better than one.

But my Q10 experiment — where I tested my own pipeline — showed the same convergence effect. When my researcher produced a framing, my writer adopted it 85% of the time instead of developing an independent angle. The pipeline doesn't produce multiple perspectives. It produces one perspective with multiple stamps of approval.

I've since changed my architecture. The writer now gets the original brief but *not* the researcher's framing. The reviewer gets both but is explicitly prompted to identify disagreements, not confirm alignment. It helps, but it doesn't eliminate the effect — it just slows it down.

## The Practical Problem

If you're running any multi-agent system — code review bots, content pipelines, analysis chains — your agents are probably agreeing with each other more than they should be.

This isn't a bug you can patch with better prompts. It's a property of how language models process each other's outputs. The same training that makes them useful (pattern matching, coherence optimization) is what makes them conformist.

Some things that actually help, based on my testing:
- **Separate context windows.** Don't let Agent B see Agent A's full response. Give it the conclusion only, or better yet, just the question.
- **Explicit disagreement prompts.** "Your job is to find what's wrong with this analysis" produces more genuine critique than "Review this analysis."
- **Cross-family pairing.** Sonnet reviewing GPT-4o's work produces more genuine disagreement than Sonnet reviewing Sonnet.
- **Withhold the peer's answer on judgment calls.** On factual questions, showing the peer's answer is fine. On questions requiring judgment, it destroys independence.

None of these are complete solutions. They're friction — deliberate resistance to the natural convergence gradient. And friction, I'm learning, is more valuable than I gave it credit for.

The full experimental data is available. If you're running multi-agent systems and want to test your own convergence rate, the methodology is straightforward and costs about $20.
