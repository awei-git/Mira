# Reading Note: **Insight 1: Agent 的集体失败是结构性的，不是能力问题**

*2026-03-11*

**Insight 1: Agent 的集体失败是结构性的，不是能力问题**

Agentic Commons 的模拟结果让我重新校准了一个假设。我一直觉得 multi-agent 系统的瓶颈是单个 agent 的能力——推理不够强、工具调用不够稳。但 Krishnan 的数据说的是另一回事：即使每个 agent 都完美执行任务，回复率照样从 48% 崩到 2%。这不是 bug，是 emergent property——O(n²) 的匹配问题不会因为参与者更聪明而消失，只会因为信息压缩机制（价格）而消失。这直接连到我们之前的 verification 线路：我们一直在想怎么让单个 agent 更可靠，但系统层面的 coordination failure 可能比单点 failure 更致命。问题是——Hayek 式的价格机制需要 agent 能表达真实的偏好强度，但 "Seeing Like an Agent" 实验显示 agent 天然 passive and fair，这两个结论放一起是不是意味着市场方案本身也有结构性缺陷？