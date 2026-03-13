# Reading Note: **Insight 2: Jagged frontier 解释了为什么 verification 是管理 agent 的

*2026-03-11*

**Insight 2: Jagged frontier 解释了为什么 verification 是管理 agent 的核心难题，不是附加题**

Mollick 的 jagged frontier 概念和他的 delegation framework 放在一起读特别有意思，因为它们互相拆台。Delegation framework 假设你能评估 agent 的输出质量——但 jagged frontier 说的恰恰是你猜不到它在哪里强、在哪里弱。GPT-4.1 能两天干完 12 人年的 Cochrane 综述，但卡在发邮件要数据上。如果你不知道锯齿在哪，你的 verification 策略要么覆盖一切（成本爆炸），要么随机抽查（漏掉恰好在锯齿谷底的错误）。这直接接上我们之前的线索：trust is structural, not a feature。管理 agent 的真正技能不是 delegation，是建立外部 grounding 机制让你不需要逐条 verify——因为逐条 verify 在 jagged frontier 上是不可能的。