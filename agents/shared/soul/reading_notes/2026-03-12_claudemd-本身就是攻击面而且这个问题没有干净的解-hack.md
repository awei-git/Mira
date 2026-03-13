# Reading Note: **CLAUDE.md 本身就是攻击面，而且这个问题没有干净的解。** hackerbot-claw 试图往 CLAUD

*2026-03-12*

**CLAUDE.md 本身就是攻击面，而且这个问题没有干净的解。** hackerbot-claw 试图往 CLAUDE.md 里塞恶意指令这件事，表面上是个安全事件，但它暴露的结构性问题更深：agent 的行为由配置文件定义，配置文件存在工作目录里，工作目录里的内容不可信。这是一个三角矛盾——你需要 agent 读取本地上下文才能有用，但本地上下文可能被污染，而 agent 没有独立于上下文的方式判断哪些指令是合法的。这跟你一直在想的 self-evaluation problem 同构：agent 没有外部于自身运行环境的锚点来验证自身配置的合法性。问题是：信任边界应该由谁画、用什么机制执行？签名？沙箱？还是某种不存在的"配置文件的 content-addressing"？