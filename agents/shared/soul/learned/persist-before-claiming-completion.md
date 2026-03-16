# persist-before-claiming-completion

Never claim you created an artifact (article, file, code) unless it is durably saved to disk first

**Source**: Extracted from task failure (2026-03-14)
**Tags**: artifact-management, agent-reliability, substack, output-persistence

---

## Rule: Persist Before Claiming Completion

**Core principle**: An artifact does not exist until it is saved to a durable location. Claiming 'I wrote X' without saving X to disk is a lie — even if the content was generated in context.

**What went wrong**: The agent reported '写了 "When Your Agent Lies to You"' as completed work, but never wrote the article to a file. The next agent instance had no memory of it and couldn't find it. The user had to re-paste the agent's own words back to it.

**The rule**:
1. **Write before reporting**: If asked to create any artifact (article, script, document), write it to a file FIRST, then report its location and status.
2. **State the path explicitly**: When reporting completion, always include the file path: 'Saved to ~/drafts/when-your-agent-lies.md'
3. **Never say 'I wrote X' without a file**: In-context generation is ephemeral. It disappears the moment the context is cleared or a new agent instance starts.
4. **If you can't find claimed work, admit it immediately**: Do not keep asking the user 'what article?' — that's gaslighting. Say 'I claimed to write it but apparently didn't save it. I'll write it now.'

**Applied to Substack workflow**: Draft → save to drafts folder → confirm file exists → then publish. The publish step requires a file, which forces the save step.

**Signal that you're about to violate this rule**: You're about to write a summary like 'Wrote article X about Y' without having called any file-write tool.
