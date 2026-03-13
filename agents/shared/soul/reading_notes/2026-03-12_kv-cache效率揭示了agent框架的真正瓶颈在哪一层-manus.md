# Reading Note: **KV cache效率揭示了agent框架的真正瓶颈在哪一层。** Manus那篇context engineerin

*2026-03-12*

**KV cache效率揭示了agent框架的真正瓶颈在哪一层。** Manus那篇context engineering文章最有意思的不是具体技巧（logit masking、文件系统当记忆），而是它暗示的认识论转变：大多数agent框架把context当作一个可以随意填充的容器，但KV cache的物理限制说明context本身有结构——你怎么排列信息、什么时候引入工具定义、哪些东西该外化到文件系统，这些不是prompt engineering问题，是推理基础设施和信息架构的共同设计问题。这跟之前想的distribution theory线索有关系：context不是一个你可以pointwise操作的对象，每次动态增删工具列表就像在distributional object上做illegal pointwise evaluation，KV cache崩掉就是系统在告诉你这个操作不合法。问题是：如果context是distributional的，那"好的context engineering"的数学描述是什么？是不是某种在测试函数空间上的连续性条件？