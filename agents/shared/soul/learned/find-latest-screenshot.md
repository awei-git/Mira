When a user says "look at this" or "check the screenshot" without a path:

1. Search these locations in parallel using Glob with patterns *.png, *.jpg, *.PNG, *.JPG:
   - ~/Desktop
   - ~/Downloads
   - /tmp
   - ~/Pictures/Screenshots
   - Root of any mounted device paths (e.g., /Volumes/*)

2. Glob returns files sorted by modification time (most recent first) — take the top result(s) from today.

3. Read the image using the Read tool (it supports PNG/JPG natively as a multimodal input).

4. Describe the visual content and analyze it against the current conversation context to identify the issue the user is pointing at.

Note: If nothing is found in the above paths, ask the user for the file path rather than guessing further.