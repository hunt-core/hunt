# Security Policy

## Supported versions

Only the latest release of hunt receives security fixes.

| Version | Supported |
|---------|-----------|
| latest  | ✓         |
| older   | ✗         |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Please report security issues by emailing **security@hunt-framework.com**. Include:

- A description of the vulnerability and its potential impact
- Steps to reproduce or a proof-of-concept (if possible)
- Any suggested fix or mitigation

You will receive an acknowledgement within 48 hours and a follow-up once the issue has been assessed. We aim to release a fix within 14 days of a confirmed vulnerability.

We will credit you in the release notes unless you prefer to remain anonymous.

## Scope

The following are in scope:

- Remote code execution
- SQL injection
- Cross-site scripting (XSS)
- Authentication bypass
- Session fixation / hijacking
- Path traversal / arbitrary file read

The following are out of scope:

- Vulnerabilities requiring physical access to the server
- Denial of service via resource exhaustion
- Issues in third-party dependencies (report those upstream)
- Security misconfigurations in user applications built on hunt

## Security design notes

- Passwords are hashed with bcrypt
- Session IDs are regenerated on login to prevent session fixation
- All template output is HTML-escaped by default; raw output requires explicit `{!! !!}` syntax
- CSRF tokens are validated on all non-GET requests
- The static file server blocks dangerous file extensions (`.py`, `.env`, `.svg`, etc.)
- The `Image` field excludes SVG from allowed upload types by default
