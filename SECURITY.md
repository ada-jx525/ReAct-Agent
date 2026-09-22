# Security policy

## Scope

This repository is a local, synthetic customer-support demo. It is not a
production identity, payment, carrier, or refund system. Do not connect it to
real customer data or expose the local API publicly without adding production
authentication, secret management, distributed rate limiting, monitoring, and
an independent security review.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting or a private Security
Advisory for vulnerabilities that could expose data, bypass customer isolation,
skip human approval, execute unintended tools, or disclose credentials. Do not
include real customer records, access tokens, model prompts, checkpoints, or
database files in a public issue.

Ordinary bugs, documentation errors, and synthetic-demo failures can be filed as
regular GitHub issues.

## Supported version

Only the latest commit on the default branch is maintained. Historical commits,
local model files, generated FAISS indexes, and user-created databases are not
covered by a support guarantee.

## Security boundaries

- Chat text cannot replace the application-provided customer identity.
- Tool and service layers re-check customer scope; model intent is not authority.
- Return submission requires an explicit user request and human confirmation.
- The HTTP API is read-only and intended for single-worker localhost use.
- Retrieved text and model drafts are untrusted and are not returned directly as
  authoritative business state.
- Local checkpoints and SQLite databases may contain internal conversation data
  and must not be committed or published.

These controls reduce risk but do not make the demo attack-proof. Evaluation
passes are regression signals, not a penetration-test certificate.

Implementation details, evaluated cases, fixed findings, and remaining risks are
documented in [Security design and review](docs/SECURITY_DESIGN.md).
