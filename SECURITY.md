# Security Policy

## Supported Versions

Security fixes are provided for the latest release and the current default branch. The deprecated `worldcup_brazil` compatibility surface receives only critical fixes during its one-release-cycle migration window.

## Reporting A Vulnerability

Use GitHub Private Vulnerability Reporting for this repository. Open the repository's **Security** tab, choose **Report a vulnerability**, and include affected versions, reproduction steps, impact, and any proposed remediation. Do not open a public issue for an undisclosed vulnerability.

Maintainers will acknowledge a complete report, investigate it privately, coordinate a fix and disclosure, and credit reporters who request attribution. No project security email is published until ownership can be independently verified.

## Trust Boundaries

- **Trusted configuration:** Tournament JSON, imported result files, templates, and provider metadata are code-like local inputs. Review them before use. Schema validation limits shape; it does not make an untrusted operational policy safe.
- **Local command boundary:** Optional bridges execute local commands only when a user explicitly enables and configures them. The generic offline CLI does not discover or execute arbitrary bridge commands automatically.
- **Symlink and output boundary:** Imports reject unsafe file substitutions, apply verifies file identity, and publication uses atomic immutable generations. Do not place output roots in attacker-controlled directories.
- **Provider key boundary:** Credentials belong in environment variables or an external secret manager. They must never be written to tournament configuration, provider payloads, logs, reports, or source control.
- **Data provenance boundary:** Normalized facts retain provider and retrieval metadata. Provenance is evidence about origin, not a guarantee that an external fact is accurate, licensed for every use, or unchanged upstream.

## Deployment Assumptions

The repository workflows build and test artifacts; they do not deploy a service or publish to PyPI. Operators who wrap the CLI in a service must add authentication, request isolation, resource limits, and an application-specific threat model.
