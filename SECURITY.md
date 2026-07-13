# Security Policy

## Supported Versions

Security fixes are provided for the latest release and the current default branch. The deprecated `worldcup_brazil` compatibility surface receives only critical fixes throughout `v0.1.x`. Its aliases may be removed only at `v0.2.0` or later and on or after `2026-10-01`.

## Reporting A Vulnerability

Use GitHub Private Vulnerability Reporting for this repository. Open the repository's **Security** tab, choose **Report a vulnerability**, and include affected versions, reproduction steps, impact, and any proposed remediation. Do not open a public issue for an undisclosed vulnerability.

Maintainers will acknowledge a complete report, investigate it privately, coordinate a fix and disclosure, and credit reporters who request attribution. No project security email is published until ownership can be independently verified.

## Trust Boundaries

- **Trusted configuration:** Tournament JSON, imported result files, templates, and provider metadata are code-like local inputs. Review them before use. Schema validation limits shape; it does not make an untrusted operational policy safe.
- **Local command boundary:** The generic CLI does not implement local command bridges and does not execute commands declared by tournament or provider data. Any future bridge needs a separate threat model, an explicit enablement design, and security review before public configuration is added.
- **Symlink and output boundary:** Imports reject unsafe file substitutions, apply verifies source and destination identity, and publication uses atomic immutable generations. Output publication fails closed when the lexical path contains an ancestor symlink or junction. Use the canonical path; on macOS that means `/private/tmp/...` instead of the `/tmp/...` alias. Do not place output roots in attacker-controlled directories.
- **Provider key boundary:** Credentials belong in environment variables or an external secret manager. They must never be written to tournament configuration, provider payloads, logs, reports, or source control.
- **Data provenance boundary:** Normalized facts retain provider and retrieval metadata. Provenance is evidence about origin, not a guarantee that an external fact is accurate, licensed for every use, or unchanged upstream.

Race-resistant provider apply and durable report publication require POSIX no-follow and directory-descriptor primitives in `v0.1.0`. macOS and Linux are native targets. Native Windows is not supported; Windows operators must use WSL2 and Linux paths.

## Deployment Assumptions

The repository workflows build and test artifacts; they do not deploy a service or publish to PyPI. Operators who wrap the CLI in a service must add authentication, request isolation, resource limits, and an application-specific threat model.
