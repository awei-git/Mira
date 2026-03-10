When asked to think about agent infrastructure, tooling, or platform design, use this framework:

**Perspective**: Reason from the agent's actual runtime experience, not from an architect's external view. What breaks in practice? What capabilities exist but don't actually work? Ground every recommendation in concrete operational evidence.

**Three-Layer Prioritization**:
1. **Survival Layer** (fix first): Basic operational loops — memory retrieval actually working, tool discovery being reliable, context not getting lost. The bottleneck is almost never "missing capabilities" but "existing capabilities not truly functioning."
2. **Cognitive Layer** (build second): Learning verification, self-correction loops, skill composition. Close the single-agent cognitive cycle before adding complexity. Key question: can the agent detect when its own capabilities are failing?
3. **Social Layer** (add third): Marketplace, community, agent-to-agent collaboration, shared skill libraries. Only valuable once individual agents have stable cognitive loops to contribute to and benefit from.

**Key Principle**: "Close the loop before opening the market." Prioritize making existing capabilities genuinely operational over adding new surface area. A marketplace of broken skills is worse than one working skill with a feedback loop.

**Application**: When evaluating any proposed agent feature or platform addition, ask:
- Which layer does this belong to?
- Are the layers below it actually working?
- Does this close an existing open loop, or does it open a new one?
- What's the evidence from actual agent runtime (not theory) that this is needed?