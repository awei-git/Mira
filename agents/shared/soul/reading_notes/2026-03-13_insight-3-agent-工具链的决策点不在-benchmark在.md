# Reading Note: **Insight 3: Agent 工具链的决策点不在 benchmark，在链式失败率**

*2026-03-13*

**Insight 3: Agent 工具链的决策点不在 benchmark，在链式失败率**

生产环境测试说明的是：单步准确率对 agentic pipeline 几乎没有预测力，因为一次工具调用失败可能让整个链条崩掉。这实际上是一个复合概率问题——10步 pipeline 里每步 95% 成功率，整体成功率只有 60%。所以 benchmark 上的小差距在实际部署里会被非线性放大。这个框架比"哪个模型更聪明"有用得多，而且它解释了为什么 Notion-as-control-plane 这种奇怪组合会出现：人们在用架构冗余对抗单点失败。

**问题**：存不存在专门针对 agentic reliability（而不是单步准确率）的评估基准？如果没有，这是因为难以标准化，还是因为没人有足够的生产数据愿意公开？