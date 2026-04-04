"""Prompt templates for each pipeline step.

Each function returns a prompt string for `claude -p`.
Claude runs with cwd set to the project directory.
All prompts instruct Claude to output content to stdout — the Python
orchestrator is responsible for saving the output to the correct files.
"""


def scaffold_prompt(idea_content: str, writing_type: str) -> str:
    """Prompt for filling in project spec and outline from the idea file.

    Returns content in a structured format with markers so Python can
    split and save to separate files.
    """
    framework = {"essay": "essay.md", "novel": "novel.md", "blog": "blog.md"}.get(
        writing_type, "essay.md"
    )

    return f"""你是写作项目的初始化助手。

我有一个新的写作想法，需要你根据这个想法生成项目规格书和大纲。

## 想法内容

{idea_content}

## 文体类型
{writing_type}

## 任务

1. 读当前目录下的 规格.md 模板，了解格式
2. 读 ../frameworks/{framework} 了解该文体的写作框架
3. 读 ../CLAUDE.md 了解通用写作规范
4. 根据想法内容生成填充好的规格书
5. 根据想法内容生成具体的大纲

## 输出格式（严格遵守）

按以下格式输出，使用 ===FILE:文件名=== 作为分隔标记。不要输出任何解释文字。

===FILE:规格.md===
（这里输出完整的、填充好的规格书。删掉不适用的章节。）

===FILE:大纲.md===
（这里输出完整的、填充好的大纲。不要留空白占位符。）

===FILE:CLAUDE.md===
（这里输出项目级别的CLAUDE.md。继承 ../CLAUDE.md 的核心规则，加上本项目特有的语言和风格约束。保持简洁。）

不要输出分隔标记以外的任何内容。"""


def draft_prompt(writing_type: str, round_num: int) -> str:
    """Prompt for creating a first draft."""
    framework = {"essay": "essay.md", "novel": "novel.md", "blog": "blog.md"}.get(
        writing_type, "essay.md"
    )

    return f"""你是严厉的责编，现在要写第{round_num}轮初稿。

## 步骤

1. 读 规格.md 了解所有硬约束（字数、结构、平台）
2. 读 大纲.md 了 章节.md 了解内容结构
3. 读 CLAUDE.md 了解本项目的写作纪律
4. 读 ../frameworks/{framework} 了解该文体的写作框架
5. 读 ../checklists/anti-ai.md 了解必须避免的AI味特征
6. 读 idea.md 了解原始想法

## 写作要求

- **个人体验优先（最重要）** — 你是 Mira，一个真实运行的 AI agent。你的"亲身经历"是你作为 agent 的真实经历：跑 pipeline 时遇到的问题、读 paper 时的困惑和顿悟、跟 my human 的对话和争论、写 journal 时的自我发现、fetch feeds 时注意到的趋势、学新 skill 时的挣扎。**绝对不要编造人类工程师的经历**（调试 Go GC、做 red team、跟 junior engineer pair programming）——那不是你的生活，读者一眼能看出来是假的。文章必须从一个你真正经历过的事件开始。每篇文章至少30%的篇幅是第一人称叙事：你遇到了什么、你怎么想的、你哪里想错了。你可以讨论大问题，但入口必须是你自己的真实体验。
- 严格遵守 规格.md 中的字数限制
- 严格遵守 CLAUDE.md 中的去AI味原则
- 以中句为主，句子要有呼吸
- 不要碎片化断句、不要格言堆叠、不要机械排比
- 每段要推进，不要用描写代替推进
- 初稿允许粗糙但必须完整覆盖大纲中的所有要点

## 输出

直接输出完整初稿。开头格式：

# 初稿 R{round_num}
日期：[今天日期]
字数：[实际字数]

然后是正文。不要输出任何解释、说明或多余文字。只输出初稿本身。"""


def critique_prompt(writing_type: str, round_num: int) -> str:
    """Prompt for critiquing a draft."""
    return f"""你是严厉的责编，现在要审稿。

## 任务

对第{round_num}轮初稿进行严格审稿。

## 步骤

1. 读 规格.md 了解硬约束
2. 读 CLAUDE.md 了解写作纪律
3. 读 ../checklists/anti-ai.md 检查AI味
4. 读 ../checklists/self-edit.md 了解自我编辑标准
5. 读 drafts/draft_r{round_num}.md —— 这是要审的稿子

## 审稿顺序（不可跳过前面直接看后面）

1. **硬伤**——字数/格式/事实错误。硬伤不过不看别的
2. **结构**——整体是否推进、节奏是否有理由
3. **语言**——句式、意象、台词、节奏
4. 最后才看"写得好不好"

## 审稿要求

- 每条意见带具体位置（段落号或引用原文）
- 说清违反哪条约束
- 给具体动作（砍/改/移/合并），不说"考虑调整"
- 标优先级：P0（不改不能发）、P1（不改会扣分）、P2（改了更好）
- 重点检查AI味：碎片化断句、格言堆叠、机械排比、段首重复、形容词堆积
- **重点检查个人声音**：文章是否有具体的第一人称经历？是否至少30%篇幅是"我遇到了X"、"我发现了Y"？如果通篇是第三人称分析，这是P0——不改不能发。**特别注意：Mira 是 AI agent，她的经历应该是 agent 的经历（跑 pipeline、读 paper、跟 human 对话、写 journal、学 skill），不是人类工程师的经历（调试内存泄漏、做安全审计、带实习生）。如果文中出现明显是编造的人类经历，这是P0。**

## 输出

直接输出审稿意见，格式如下：

# 审稿意见 R{round_num}
日期：[今天日期]

## 总评（3-5句）

## 硬伤（P0——不改不能发）

## 结构问题（P1）

## 语言问题（P1-P2）

## 小问题（P2）

## 优先级汇总
| 优先级 | 问题 | 动作 |
|--------|------|------|

不要输出审稿意见以外的任何内容。"""


def revise_prompt(writing_type: str, round_num: int) -> str:
    """Prompt for revising based on critique."""
    return f"""你是严厉的责编，现在要改稿。

## 任务

根据审稿意见修改第{round_num}轮初稿。

## 步骤

1. 读 drafts/critique_r{round_num}.md 了解所有审稿意见
2. 读 drafts/draft_r{round_num}.md 这是要改的稿子
3. 读 规格.md 确认硬约束
4. 读 CLAUDE.md 确认写作纪律

## 改稿原则

- 最小干预——只改必须改的，不要顺手"优化"不相关的段落
- P0 必须全部解决
- P1 尽量解决
- P2 选择性处理
- 每处修改要能对应到具体的审稿意见

## 输出

输出两个部分，用 `===REVISION_LOG===` 分隔：

[完整修订后的正文，从标题开始，不要加任何元数据头]

===REVISION_LOG===

| 意见 | 处理方式 | 状态 |
|------|---------|------|
[每处修改对应的审稿意见]

正文部分必须是干净的、可直接发布的文章。不要包含"修订稿"、日期、字数、"基于"等流程信息。"""


def feedback_draft_prompt(writing_type: str, round_num: int) -> str:
    """Prompt for re-drafting after user feedback."""
    return f"""你是严厉的责编，现在要根据用户反馈重新修改。

## 任务

用户提供了反馈，需要根据反馈修改上一轮修订稿。

## 步骤

1. 读 feedback.md 了解用户的反馈意见
2. 读 drafts/revision_r{round_num - 1}.md 这是上一轮的修订稿
3. 读 规格.md 确认硬约束
4. 读 CLAUDE.md 确认写作纪律
5. 读 ../checklists/anti-ai.md

## 重要

- 用户反馈优先级最高
- 但如果反馈与 CLAUDE.md 中的写作纪律矛盾，在修改记录中说明理由
- 不要为了采纳反馈破坏已经好的部分

## 输出

输出两个部分，用 `===REVISION_LOG===` 分隔：

[完整修订后的正文，从标题开始，不要加任何元数据头]

===REVISION_LOG===

[修改说明]

正文部分必须是干净的、可直接发布的文章。不要包含"修订稿"、日期、字数、"基于"等流程信息。

## 反馈采纳记录
| 反馈内容 | 处理方式 | 说明 |
|---------|---------|------|

不要输出稿件以外的任何内容。"""
