# Episode: substack上发文章怎么增加exposure？ 也问问o
*Task: task_4b9ad97d | Date: 2026-03-14*

**[2026-03-10T16:57] ang**: substack上发文章怎么增加exposure？ 也问问openai， 整理一下方法 存成skills或者workflow 放你的socialmedia agent里面去

**[2026-03-10T17:01] agent**: 完成。搜索了10+篇最新文章，整理出完整方法论，核心发现：

**2025-2026年Substack的增长引擎已经从外部推广转向平台内部发现。** 一个季度内3200万新订阅者来自app内部，内部读者转化率是外部的4倍。Notes算法会主动推荐陌生创作者——这是小创作者最大的机会。

输出文件：
- **output.md** — 完整的中文方法论研究报告
- **skill文件** — `agents/socialmedia/skills/substack-growth-playbook.md`，包含五层增长模型、每日执行框架、8个具体战术（Notes/评论/推荐/SEO/AI可发现性/跨平台/合作/欢迎邮件）、反模式列表、以及对现有growth.py的改进建议

**[2026-03-10T17:16] ang**: 那mira你赶紧加上notes这块吧 把之前文章的notes也加上

**[2026-03-10T17:27] agent**: 处理失败: Task timed out

**[2026-03-10T17:46] ang**: 那mira你赶紧加上notes这块吧

**[2026-03-10T17:56] agent**: 处理失败: Task timed out

**[2026-03-10T18:14] ang**: substack上发文章怎么增加exposure？ 也问问openai， 整理一下方法 存成skills或者workflow 放你的socialmedia agent里面去

**[2026-03-10T18:14] ang**: 为什么会处理失败

**[2026-03-10T18:21] agent**: 完成。核心发现：

**OpenAI (GPT-4o) 的回答质量一般** — 泛化建议多，缺乏具体数据和平台机制理解。Round 1的Web研究在深度上完胜。但OpenAI贡献了两个有价值的补充点：**双语内容策略**（bilingual本身就是差异化）和**主题系列化**（series比散文有更强的留存和SEO）。

输出文件：
- [output.md](file://output.md) — 完整对比分析报告（Round 1 vs OpenAI + 综合方法论）
- [substack-growth-playbook.md](file://../../agents/socialmedia/skills/substack-growth-playbook.md) — 更新后的skill文件（从8个战术扩展到10个）

关于**之前两次timeout**：任务是"加上Notes功能 + 补发旧文章Notes"，涉及代码修改，耗时超过task_worker的默认timeout。Notes功能其实已经在 `notes.py` 中完整实现了（article-linked + standalone + backfill），今天已经发了4条Notes。如果需要补发旧文章的Notes，可以单独发一个任务。

**[2026-03-10T18:24] agent**: 查完了。结论：

**你的研究任务没有失败。** Round 1（Substack增长研究）和Round 2（OpenAI对比）都正常完成了，`exec_log.jsonl` 里两条记录都是 `status: "done"`。

两次"处理失败"是你中间说"赶紧加上Notes"时触发的**代码修改任务**。从 `worker.log` 看，`claude_act` 在读代码、理解架构、写新模块的过程中超过了 `CLAUDE_TIMEOUT_ACT = 600秒`（10分钟）的限制，被 task_worker kill 了。研究任务几分钟就能完成，但代码开发任务需要多轮文件读写，10分钟不够。

如果还想加Notes功能，建议拆成更小的步骤分别执行（比如先"读现有publish代码告诉我架构"，再"写一个notes.py模块"）。

详细分析见 [output.md](file://output.md)。

**[2026-03-10T18:29] ang**: 加一个写notes的模块 在socialmedia那个agent里

**[2026-03-10T18:33] agent**: 完成。`agents/socialmedia/notes.py` 已经是一个完整的 Notes 模块（551行），包含：

- 发布 Note（纯文本/富文本/链接卡片）
- LLM 生成文章推广文案
- 历史文章 Notes 批量补发
- 从 briefing 素材生成独立 Note
- 每日自动周期（`run_notes_cycle`）
- 频率控制（5条/天，30分钟间隔）

`core.py` 已经集成了自动调度（`should_post_notes()` → `do_notes_cycle()`），`handler.py` 已注册 `substack_note` 平台。不需要新增任何代码。

之前两次 timeout 是因为 task_worker 的 10 分钟限制不够读代码+写代码的时间，不是因为模块不存在。

**[2026-03-10T18:39] ang**: 那补几条notes来推广publication吧
