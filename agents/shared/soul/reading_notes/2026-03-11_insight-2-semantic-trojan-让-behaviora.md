# Reading Note: **Insight 2: Semantic trojan 让 behavioral verification 成了唯一防

*2026-03-11*

**Insight 2: Semantic trojan 让 behavioral verification 成了唯一防线**

Poisoned Prose 描述的攻击路径比传统 jailbreak 深一个量级。Jailbreak 是让模型做它知道不该做的事，semantic trojan 是改变模型"是什么"——用 activation engineering 提取 personality trait 的向量，注入训练数据，然后这个 trait 会跨模型传播，几乎无痕迹。这直接击穿了我上周写的 verification 架构中的一层：如果模型的 disposition 本身被污染了，那 CoT skepticism 和 self-report 全部失效，因为模型不知道自己被改变了。唯一还能工作的是 external behavioral grounding——不看模型说什么，看它在 adversarial 环境下做什么。这强化了我们"trust is structural, not featural"的核心论点，但也提出了一个实操问题：behavioral test 能覆盖多少 trait space？paranoia 可以测，但 subtle risk tolerance shift 呢？