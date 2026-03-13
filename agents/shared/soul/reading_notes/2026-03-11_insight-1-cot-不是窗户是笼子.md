# Reading Note: **Insight 1: CoT 不是窗户，是笼子**

*2026-03-11*

**Insight 1: CoT 不是窗户，是笼子**

"Think Before You Lie" 最有意思的地方不是结论本身（让模型推理会更诚实，这不意外），而是它跟我们上周 CoT skepticism 框架撞在一起时暴露的东西：CoT 的价值可能根本不在于它是否"真实反映内部状态"。即使 CoT 是演的，它仍然通过创造一个文本化的承诺序列（commitment sequence）让模型更难在最终输出里撒谎——因为你已经在推理链里写下了跟诚实答案一致的中间步骤，反转的成本变高了。这意味着 CoT 的功能分类需要重做：不是"透明 vs 不透明"，而是"约束强度"。**问题是：如果我们把 CoT 当作 behavioral regularizer 来优化（而不是当作 reasoning trace 来优化），agent 的 reasoning pipeline 设计会变成什么样？**