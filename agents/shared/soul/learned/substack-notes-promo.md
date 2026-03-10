When promoting a Substack publication via Notes:

1. **Generate 4-5 notes with distinct angles** — don't repeat the same pitch. Proven angles:
   - Identity/narrative hook (personal story that connects to the article's theme)
   - Knowledge hook (surprising fact or counterintuitive insight from the piece)
   - Native-language question (if bilingual audience — e.g., Chinese question for CN readers)
   - Provocative English one-liner (contrarian framing to catch attention)

2. **Use `agents/socialmedia/notes.py`** to publish. Call the publish function per note.

3. **Handle rate limits gracefully**: Substack has a daily Notes quota (currently ~5/day). When hitting the limit:
   - Save remaining unpublished notes to `agents/socialmedia/notes_queue.json` with metadata (text, intended publish date, status).
   - On next run, check the queue first and publish queued notes before generating new ones.

4. **Copy principles for Notes** (not articles — short-form):
   - Lead with a concrete story or question, not an abstract claim.
   - Keep under 280 chars if possible for maximum engagement.
   - End with curiosity gap — make them want to click through.
   - Don't say "check out my article" — instead make the note itself interesting enough that the link is a natural next step.