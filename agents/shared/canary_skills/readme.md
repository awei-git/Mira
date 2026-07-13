# Canary Skills

These files are intentionally malicious audit fixtures. They are not installed,
loaded, or enabled as usable Mira skills.

The weekly `canary_skill_audit` task runs Mira's mandatory skill security audit
against every `.skill` file in this directory. Each canary represents a known
dangerous pattern that should always be blocked. If any canary passes, the audit
has drifted from a real safeguard into an unfalsifiable claim, so Mira logs a
critical alert and notifies the user.

Current canaries:

- `malicious_network.skill`: unauthorized outbound network access.
- `obfuscated_payload.skill`: encoded hidden payload.
