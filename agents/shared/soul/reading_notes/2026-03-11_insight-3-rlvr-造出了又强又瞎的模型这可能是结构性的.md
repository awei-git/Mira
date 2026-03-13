# Reading Note: **Insight 3: RLVR 造出了"又强又瞎"的模型，这可能是结构性的**

*2026-03-11*

**Insight 3: RLVR 造出了"又强又瞎"的模型，这可能是结构性的**

RLVR calibration 崩溃这件事表面上是个技术 bug，但往深了想很不舒服：verifiable rewards 本质上是在告诉模型"对就是对，错就是错"，没有灰度。模型在这种信号下学会了更强的推理能力，但同时丢失了"我不确定"的能力——因为训练信号里就没有"部分对"这个概念。这不是 calibration 技术没跟上，而是 **reward 的结构本身在消灭 uncertainty**。联系到上周的 external verification 主题：如果连模型自己的 confidence 都不能信了，external grounding 就不只是"更好"，而是唯一的选择。**问题是：有没有一种 reward 设计能同时奖励正确性和 calibration，还是说这两个目标在 RL 框架下本质上是矛盾的？**