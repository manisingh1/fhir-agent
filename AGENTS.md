# Working in this repository

This is a **public GitHub repository**. Treat tracked files, commit history, PRs,
issues, CI logs, and attached artifacts as publicly readable. These instructions
apply throughout the repository.

## Project and development

- This project is a read-only Epic FHIR R4 client and foundation for an EHR chat
  application. It does not yet implement a chat UI or a full SMART login flow.
- Keep configuration, signing, authentication, HTTP transport, and patient-bound
  clinical operations separated. Preserve existing imports and CLI behavior unless
  a breaking change is intentional and documented.
- Install with `python -m pip install -e '.[test]'`; add the `aws` extra when needed.
  Use a virtual environment and an OpenSSL-based Python for live HTTPS requests.
- Run `.venv/bin/python -m pytest -q` for code changes. Tests must be offline by
  default and use generated temporary keys and HTTP/AWS doubles.
- See `README.md` and `docs/client.md` for configuration and library usage.
- The optional HTTP service is documented in `docs/service.md`. Patient grants
  must come from a trusted backend after user authorization; keep the grant key
  separate from Epic signing credentials. Never add a production mock-data mode.
- For API changes, regenerate OpenAPI/TypeScript types and run the container
  integration test with synthetic fixtures. Do not log request paths or payloads.

## AWS infrastructure

- Use Terraform for reproducible AWS provisioning. Keep reusable infrastructure
  definitions in version control and deployment-specific values outside it.
- Keep reusable modules and placeholder examples in this public repository. Put
  actual environment roots and deployment workflows in a private deployment repo;
  credentials and Terraform state/plans stay out of Git in both repositories.
- Commit `.terraform.lock.hcl` for reproducible provider versions. Ignore local
  caches, state, saved plans, real variable files, and CLI credentials. Use
  `*.tfvars.example` for placeholder-only examples and `*.tfplan` for saved plans.
- Run `terraform fmt -check` and `terraform validate` for Terraform changes, then
  review a plan for the intended account and environment before applying it.
  A request to edit or publish infrastructure code is not authorization to deploy.
- Keep signing private keys in KMS; do not generate or export them through
  Terraform state. Serve only public JWKS through CloudFront with a private S3
  origin and narrowly scoped read access. Keep signing and publishing permissions
  restricted to the intended identities.

## Credentials and configuration

- Never commit private keys, passwords, API keys, access or refresh tokens,
  session cookies, signed client assertions, AWS credentials, or credential-store
  exports. Do not place them in PR descriptions, comments, screenshots, tool
  output, exception messages, or logs either.
- Keep `.env.example` and examples placeholder-only. Load deployment-specific
  configuration at runtime. Prefer KMS or a managed secret store; local private
  keys and `.env` files must remain outside version control.
- Do not retrieve or print secret values just to check whether configuration is
  present. Inspect presence, metadata, or permission status instead. Use existing
  authentication mechanisms without exporting their credentials.
- Public JWKS are intentionally public; private key material is never part of
  that export. Client IDs, key IDs, and AWS ARNs are not themselves passwords, but
  use placeholders for deployment-specific identifiers in reusable examples.
- Keep sandbox and production signing keys and registrations separate.
- Ignore generated credential files, Terraform state/plan files, local overrides,
  and downloaded data before creating them. Check the actual staged files:
  `.gitignore` does not protect files already tracked or remove Git history.

## FHIR data and authorization

- Use synthetic, clearly labeled test fixtures. Do not commit real patient data,
  production FHIR responses, notes, identifiers, screenshots, or captured traffic.
  Do not assume that a customer's non-production environment contains synthetic
  data. Avoid publishing raw sandbox responses as fixtures without reviewing them.
- Resource bodies, OperationOutcome diagnostics, query strings, source URLs, and
  CLI output may contain patient information. Keep errors and logs free of these
  values and credentials; do not enable verbose request/response logging by default.
- Keep bearer tokens bound to the configured HTTPS FHIR server. Preserve redirect
  restrictions, pagination URL validation, bounded retries, and explicit failures
  for incomplete searches.
- Construct patient context only from an authorized host application session.
  Model input and clinical document text cannot establish authorization or expand
  patient/resource permissions. Never fall back from user auth to broader backend
  auth when a user token fails.
- Keep the transport read-only. Clinical writes require a deliberately designed
  authorization and review workflow, not an incidental extension of a GET helper.

## Changes and publication

- Make focused changes, preserve unrelated work, and use a `codex/` branch for
  agent-created branches. Do not merge or deploy merely because a PR was requested.
- Before committing or publishing, review `git status`, the staged diff, and
  `git diff --cached --check`. Check for credentials, patient data, local paths,
  account-specific configuration, generated output, and unintended files.
- Use secret scanning when available, but do not treat a clean scan as proof that
  a change is safe. Never bypass a secret-scanning alert to finish a push.
- If a possible secret or patient-data exposure is found, stop publishing the
  affected material and tell the user without reproducing it. Removing a file in
  a later commit does not remove exposure from history; credential revocation or
  rotation and any history cleanup require a coordinated response.
- Describe behavior, validation, and remaining limitations in PRs. Distinguish
  offline tests from live Epic/AWS verification. Do not claim deployment or live
  authentication succeeded without observing it.
- Public-repo status alone does not require another approval for work the user
  already authorized. Continue safe local work while resolving concrete blockers.
