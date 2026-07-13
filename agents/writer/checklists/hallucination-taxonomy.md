# Hallucination Taxonomy Checklist

Run this scan before finalizing writer output. Verify claims against provided sources, official documentation, or reliable external references. If verification is unavailable, mark the claim `[citation needed]`, hedge it, or remove it.

## Legal

- [ ] Any claim referencing statutes, cases, treaties, regulations, legal doctrines, or legal principles includes a verifiable citation.
- [ ] Generic-sounding phrases such as "law of X", "X doctrine", "the principle of Y", or "under international law" are flagged unless tied to a real source.
- [ ] Jurisdiction, date, court/body, citation number, and quoted legal text are checked before publication.

## History

- [ ] Any specific event, date, quote, casualty figure, named participant, or timeline claim not supplied by the provided source material is marked `[citation needed]` or removed.
- [ ] Quotes attributed to historical figures are verified against a reliable source before use.
- [ ] Plausible but unsourced anecdotes are treated as unverified, not as color.

## Programming

- [ ] Any function, API, class, CLI command, configuration key, library/package name, or version-specific behavior matches real documentation.
- [ ] Function signatures, parameter names, imports, and return values are checked against current official docs or source.
- [ ] Invented functions, nonexistent libraries, or deprecated APIs presented as current are flagged and removed or corrected.

## Overgeneralisation

- [ ] Phrases such as "studies show", "experts agree", "research proves", "it is well known", "many people say", or "the consensus is" trigger a verification step.
- [ ] Broad claims are narrowed to the cited evidence; if no evidence is available, mark `[citation needed]` or rewrite as a clearly bounded observation.
- [ ] Statistical or causal claims include a source, scope, and date.

## Cultural/timezone dominance

- [ ] Check whether the draft assumes a US, English-language, Western, Gregorian-calendar, or single-timezone default that may not hold for the audience.
- [ ] Dates, holidays, workweeks, legal defaults, education systems, currencies, measurement units, and cultural references are localized or caveated when needed.
- [ ] Relative time phrases such as "today", "this year", or "currently" are converted to exact dates when factual precision matters.
