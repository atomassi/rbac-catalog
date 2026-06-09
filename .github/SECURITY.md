# Security Policy

## Project status

This repository is provided **as-is** and is **not actively maintained**. The
public site at `rbac-catalog.dev` is being decommissioned (June 12, 2026), and
only the source code remains here under the [MIT License](../LICENSE).

There is **no support, no guaranteed response, and no commitment to release
security fixes.** Use this code at your own risk.

## Supported versions

| Version | Supported |
|---|---|
| `main` (and all tags) | :x: No active support |

## Reporting a vulnerability

Because the project is unmaintained, there is no private security contact and no
service-level commitment to triage reports.

If you find a vulnerability, the most effective options are:

- **Report privately:** https://github.com/atomassi/rbac-catalog/security/advisories/new
- **Fork and fix it yourself.** Pull requests are welcome and may be reviewed on
  a best-effort basis, but there is no guaranteed timeline.
- **Open a GitHub issue** for awareness — but **do not include working exploit
  details**, since no fix may be forthcoming and the repository is public.

If you operate your own deployment of this code, you are responsible for
assessing and remediating vulnerabilities in your instance, including keeping
dependencies up to date.

## Your own deployment

If you self-host this project:

- Review the hardening guidance in [`infra/README.md`](../infra/README.md)
  (TLS, network access restrictions, PostgreSQL firewall, secret handling).
- Keep dependencies patched — Dependabot configuration ships in
  [`.github/dependabot.yml`](dependabot.yml), but you must apply updates
  in your own fork.
- Never expose admin credentials or secrets; the app supports Entra ID /
  managed-identity auth for database access.
