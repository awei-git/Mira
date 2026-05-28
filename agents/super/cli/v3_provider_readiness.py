"""Check V3 provider production-readiness provisioning."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mira.runtime import (
    default_v3_paths,
    provider_production_readiness_report,
    write_provider_provisioning_runbook,
    write_provider_provisioning_env_template,
    write_provider_adapter_config_template,
    write_provider_resolver_config_template,
)


def render_report(report: dict) -> str:
    lines = [
        "Mira V3 Provider Readiness",
        "==========================",
        "",
        f"Ready: {'yes' if report['ready'] else 'no'}",
        f"Resolver config: {report['resolver_config']}",
        f"Adapter config: {report['adapter_config']}",
        f"Configured resolvers: {', '.join(report['configured_resolvers']) or '(none)'}",
        f"Configured adapters: {', '.join(report['configured_adapters']) or '(none)'}",
    ]
    if report.get("created_templates"):
        lines.append(f"Created templates: {', '.join(report['created_templates'])}")
    if report.get("env_template"):
        lines.append(f"Env template: {report['env_template']}")
    if report.get("runbook"):
        lines.append(f"Runbook: {report['runbook']}")
    findings = report.get("findings") or {}
    for surface, surface_findings in findings.items():
        active = {provider: items for provider, items in surface_findings.items() if items}
        if not active:
            continue
        lines.extend(["", f"{surface}:"])
        for provider, items in sorted(active.items()):
            for item in items:
                lines.append(f"- {provider}: {item}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Mira V3 provider production readiness.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Mira workspace root.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--resolver-config", type=Path, help="Provider resolver config path.")
    parser.add_argument("--adapter-config", type=Path, help="Provider adapter config path.")
    parser.add_argument("--require-resolver", action="append", default=None, help="Required resolver provider.")
    parser.add_argument("--require-adapter", action="append", default=None, help="Required adapter provider.")
    parser.add_argument(
        "--skip-resolvers",
        action="store_true",
        help="Do not require any resolver providers; useful for adapter-only canaries such as TTS.",
    )
    parser.add_argument(
        "--skip-adapters",
        action="store_true",
        help="Do not require any adapter providers; useful for resolver-only checks.",
    )
    parser.add_argument("--allow-inline-secrets", action="store_true", help="Allow inline secrets during validation.")
    parser.add_argument(
        "--write-missing-templates",
        action="store_true",
        help="Create missing no-secret provider config templates before checking readiness.",
    )
    parser.add_argument(
        "--write-env-template",
        type=Path,
        help="Write a no-secret dotenv provisioning template for required provider env vars.",
    )
    parser.add_argument(
        "--overwrite-env-template",
        action="store_true",
        help="Overwrite an existing env provisioning template.",
    )
    parser.add_argument(
        "--write-runbook",
        type=Path,
        help="Write a no-secret Markdown provisioning runbook with scoped readiness/canary commands.",
    )
    parser.add_argument(
        "--overwrite-runbook",
        action="store_true",
        help="Overwrite an existing provider provisioning runbook.",
    )
    args = parser.parse_args()

    paths = default_v3_paths(args.root)
    resolver_config_path = args.resolver_config or paths.provider_resolvers
    adapter_config_path = args.adapter_config or paths.provider_adapters
    required_resolvers = (
        () if args.skip_resolvers else tuple(args.require_resolver) if args.require_resolver is not None else None
    )
    required_adapters = (
        () if args.skip_adapters else tuple(args.require_adapter) if args.require_adapter is not None else None
    )
    created: list[str] = []
    if args.write_missing_templates:
        if not resolver_config_path.exists():
            write_provider_resolver_config_template(resolver_config_path)
            created.append(str(resolver_config_path))
        if not adapter_config_path.exists():
            write_provider_adapter_config_template(adapter_config_path)
            created.append(str(adapter_config_path))
    env_template_path: Path | None = None
    if args.write_env_template is not None:
        env_template_path = write_provider_provisioning_env_template(
            args.write_env_template,
            resolver_config_path=resolver_config_path,
            adapter_config_path=adapter_config_path,
            required_resolvers=required_resolvers,
            required_adapters=required_adapters,
            root=args.root,
            overwrite=args.overwrite_env_template,
        )
    runbook_path: Path | None = None
    if args.write_runbook is not None:
        runbook_path = write_provider_provisioning_runbook(
            args.write_runbook,
            resolver_config_path=resolver_config_path,
            adapter_config_path=adapter_config_path,
            required_resolvers=required_resolvers,
            required_adapters=required_adapters,
            root=args.root,
            allow_inline_secrets=args.allow_inline_secrets,
            overwrite=args.overwrite_runbook,
        )
    report = provider_production_readiness_report(
        root=args.root,
        resolver_config_path=resolver_config_path,
        adapter_config_path=adapter_config_path,
        required_resolvers=required_resolvers,
        required_adapters=required_adapters,
        allow_inline_secrets=args.allow_inline_secrets,
    )
    report["created_templates"] = created
    if env_template_path is not None:
        report["env_template"] = str(env_template_path)
    if runbook_path is not None:
        report["runbook"] = str(runbook_path)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_report(report))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
