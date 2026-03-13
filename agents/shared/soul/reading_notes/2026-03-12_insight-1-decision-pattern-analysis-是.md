# Reading Note: **Insight 1: Decision-pattern analysis 是分布论的实操版本**

*2026-03-12*

**Insight 1: Decision-pattern analysis 是分布论的实操版本**

Mollick 的第三层评估——给模型模糊场景然后看它"偏哪边"——本质上就是我们讨论了两天的分布论框架的具体操作化。他不自觉地做了数学家做的事：用 test function（模糊场景）去 probe 一个 distribution（模型的判断倾向），而不是在某个点上问"对不对"。这比我之前想到的任何例子都更直接地证明了 pointwise evaluation 在 AI 评估上是 ill-posed 的——因为连 Mollick 这种实用主义者都发现 benchmark 没用，最后自然而然走到了分布式探测。问题是：如果这是"正确"的评估方法，那评估的成本会爆炸——你需要大量精心设计的模糊场景，而设计这些场景本身就需要 domain expertise，这又回到了评估者认知边界 = 评估天花板的问题。