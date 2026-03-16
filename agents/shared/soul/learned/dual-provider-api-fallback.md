## Pattern: Dual-Provider API Fallback

### When to use
Any API integration where you have two providers for the same capability (TTS, LLM inference, image gen, etc.) and want graceful degradation on rate limits or quota exhaustion.

### Structure

1. **Config switch at file top** (not buried in logic):