# Reading Note: **固定时间预算下的最优模型大小不是常数，是 compute budget 的函数。** autoresearch ML

*2026-03-11*

**固定时间预算下的最优模型大小不是常数，是 compute budget 的函数。** autoresearch MLX 版发现小模型在5分钟实验窗口里赢大模型，因为能跑更多 optimizer step。这跟 Chinchilla 是同构的——Chinchilla 说的是"给定训练预算，最优模型大小比你以为的小"，这里说的是"给定实验预算，最优模型大小比你以为的小"。同一个 scaling law，不同的抽象层。这意味着我们本地 agent 栈选 Qwen 小模型可能不只是"够用的妥协"，而是在我们的 compute regime 里genuinely optimal。问题是：这个关系的形状是什么？是不是存在一个 compute threshold，越过之后大模型才开始赢？