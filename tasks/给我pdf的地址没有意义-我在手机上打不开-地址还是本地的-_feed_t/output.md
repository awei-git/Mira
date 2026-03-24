两个问题都修好了：

**Fix 1 — Synthesis LLM** (`src/report/llm_clients.py:228`):
把 `critical` tier 路由从 `["claude-cli", "claude", ...]` 改成 `["gemini", "claude", "openai", "deepseek", "claude-cli"]`。claude-cli 降到最后。下次报告跑起来会直接用 Gemini API（或 Claude API/DeepSeek），不会再碰那个 300s 的 subprocess。

**Fix 2 — 本地 PDF 路径** (`src/mira/push.py`):
- `_generate_briefing_md()` 末尾那几行「Full report: `/local/path.pdf`」删掉了
- bridge item 的 `result_path` 固定为 `None`

报告的实际内容（regime、持仓动作、交易建议、风险提示）本来就全在 `briefing_md` 里以 markdown 发出来，手机上直接可读。PDF 还会继续生成（用于邮件），只是不再塞进消息里了。