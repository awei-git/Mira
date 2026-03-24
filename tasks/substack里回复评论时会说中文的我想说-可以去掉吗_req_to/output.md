修复完成。

**根本原因**：`agents/shared/prompts.py:204` 的格式模板用了 `我想说：` 作为占位标签，Claude 照着模板生成 briefing，这段文字就原封不动地混进了评论内容。

**两处改动**：
- `prompts.py:204` — 去掉模板里的 `我想说：`，下个 explore 周期起新生成的 briefing 不再含这个前缀
- `core.py:_extract_comment_suggestions()` — 提取 `comment_draft` 时用 `re.sub` 清除旧格式残留，兼容已生成但还没执行的旧 briefing