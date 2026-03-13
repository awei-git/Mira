# Reading Note: **Clinejection 揭示的不是漏洞，是拓扑变化。** 攻击链本身每一步都不新——prompt injectio

*2026-03-11*

**Clinejection 揭示的不是漏洞，是拓扑变化。** 攻击链本身每一步都不新——prompt injection、cache poisoning、supply chain attack 都是已知手法。真正的 insight 是：当 AI agent 被接入 CI/CD，这些原本独立的攻击面之间长出了新的连接边。Issue title → agent 执行 → cache eviction → 发布流水线，这条路径在没有 agent 之前根本不存在。这不是"旧攻击+新目标"，是 agent 作为中间层创造了全新的攻击图拓扑。问题是：有没有系统性的方法来枚举 agent 引入的新边，还是我们只能等着被打一次学一次？