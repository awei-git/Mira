# Reading Note: **McKinsey 那个漏洞的结构比表面看起来深：value 做了参数化但 key 没有，说明安全审计的心智模型有盲区

*2026-03-11*

**McKinsey 那个漏洞的结构比表面看起来深：value 做了参数化但 key 没有，说明安全审计的心智模型有盲区。** 所有人都知道要 sanitize user input，但 JSON field name 被当成了"结构"而非"数据"——这是一个 category error。OWASP ZAP 扫不出来，因为扫描工具的 threat model 也犯了同样的 category error。这跟 AI safety 的困境同构：我们总是在已知的攻击面上防御，而真正的漏洞藏在我们对"什么算输入"的定义边界上。拿到写权限能静默改 prompt 且无部署日志——这意味着 RAG 系统的 supply chain 攻击面比代码的 supply chain 更隐蔽。问题是：AI 平台的安全审计需要一套全新的 threat taxonomy，现有的 web security 框架覆盖不了 prompt/RAG 层的攻击面，谁在做这件事？