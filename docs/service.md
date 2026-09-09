# Containerized FHIR service

The optional FastAPI service wraps the existing read-only Python client. A trusted
TypeScript **backend** calls seven explicit patient routes; browsers and model
tools must not possess the application grant-signing key. No arbitrary URL proxy,
write API, public ingress, or user login implementation is included.

## Authorization contract

The host application must authenticate its user and authorize the requested
patient and resource types **before** minting an HS256 JWT. Claims are `iss`
(default `fhir-host-app`), `aud` (default `fhir-service`), `sub` (authenticated user),
`iat`, `exp` (at most 300 seconds after `iat`), `patient` (FHIR ID), and `resources`
(an explicit list drawn from Patient, Condition, Encounter, MedicationRequest,
AllergyIntolerance, Observation). Send it as `Authorization: Bearer …`.

Use a cryptographically random shared key of at least 32 bytes, mounted at
`FHIR_GRANT_KEY_FILE` in both trusted services. Set `FHIR_GRANT_ISSUER` and
`FHIR_GRANT_AUDIENCE` consistently. This key is separate from Epic's KMS key.
It grants authority to mint patient permissions: protect it as a credential,
never issue tokens from untrusted chat input, and never send it to the browser.
The initial implementation supports one key; rotate both services together.
Grants are replayable until expiry, so use short lifetimes and transport TLS.

Routes: `GET /v1/patients/{patient_id}` and `/conditions`, `/encounters`,
`/medications`, `/allergies`, `/labs`, `/vitals` below that path. Patient reads
return `{resource}`; searches return `{resources, traversal_complete}`. Searches
verify subject references and fetch at most five pages, failing rather than
returning an incomplete chart. Medication results are orders, not reconciliation.
401 means invalid/missing grant; 403 means the grant lacks permission; 502 means
upstream failure or unverifiable results. Error bodies omit upstream diagnostics.
Responses have `Cache-Control: no-store`. The launcher disables request and
traceback logs; configure infrastructure logs to omit headers, paths, and bodies.
Operational counters can be added separately without clinical identifiers.

`/healthz` and `/readyz` reveal only process/configuration readiness, not Epic
connectivity. Startup requires valid configuration. No credentials are acquired
until a clinical request. Each request owns and closes a client; this prioritizes
isolation over token reuse and may require pooling for higher request volumes.

## Local synthetic integration test

Requires Docker with a running daemon, Docker Compose, Node 22+, and Python 3.9+.
On macOS a Colima runtime works with the Docker CLI. The standalone
`docker-compose` command can substitute for `docker compose`.

```sh
export FHIR_LOCAL_UID="$(id -u)" FHIR_LOCAL_GID="$(id -g)"
python3 scripts/init_local_service.py
# Test-only override mounts an external synthetic adapter; production image has no mock mode.
docker compose -f compose.yaml -f compose.test.yaml up --build -d --wait
cd examples/typescript
npm ci
npm run check
npm run smoke
cd ../..
docker compose -f compose.yaml -f compose.test.yaml down
```

This tests TypeScript → HTTP container → synthetic client, plus missing grants,
wrong patients, and denied operations. It does **not** verify Epic or KMS. The
fixture uses invented data, no AWS credentials, and never contacts Epic. The
production image excludes tests, local keys, `.env`, Terraform, and Git history.
Compose exposes only loopback. Local UID/GID settings let the non-root container
read the owner-only development key without relaxing file permissions. Local HTTP is for development only.

To regenerate the checked-in types after changing routes:

```sh
python -m pip install -e '.[service,test]'
python scripts/export_openapi.py
cd examples/typescript && npm ci && npm run generate && npm run check
```

`client.ts` is a server-side typed wrapper using generated OpenAPI response types.
FHIR resources intentionally remain extensible JSON rather than a full R4 schema.
`smoke.ts` mints synthetic grants only; replace that logic with your application's
verified user/patient authorization. It prints a summary, never resource bodies.

## Local live Epic sandbox

First deploy the authentication infrastructure, publish JWKS, and register it in
Epic as described in [the infrastructure guide](../infra/README.md). Supply
`EPIC_CLIENT_ID`, `EPIC_KEY_ID`, `EPIC_KMS_KEY_ID`, and `AWS_REGION` outside Git.
The Compose service uses the client's default Epic sandbox endpoints.

Create a private Compose override to mount a dedicated AWS profile directory
read-only into `/home/app/.aws` and set `AWS_PROFILE`. Use a profile that can sign
only with the intended sandbox KMS key. SSO profiles require a valid host login
and readable cache; do not put AWS credentials in image layers. The service also
supports the SDK credential chain, including EKS Pod Identity. Do not mount all
personal credentials into a shared or untrusted container.

Run `docker compose up --build -d --wait` with the private override, **without**
`compose.test.yaml`. Have your trusted backend authorize a real synthetic Epic
sandbox patient and request that ID. A successful real Patient read is the live
checkpoint; the synthetic smoke harness always uses its invented patient ID.

## EKS deployment

Use the same reviewed image, built for the node architecture (for example
linux/amd64), pushed to your registry and pinned by digest. Copy the template
[service.yaml](../deploy/kubernetes/service.yaml) into the private deployment repo.
It assumes an existing cluster and namespace; this PR does not provision EKS.

The private deployment stack must provide:

- ConfigMap `fhir-service-config` with Epic client/key identifiers and AWS region.
- Secret `fhir-service-grant-key` with a `grant-key` entry delivered by your secret
  manager. Do not commit a Secret manifest containing actual material.
- EKS Pod Identity Agent, an IAM role trusting `pods.eks.amazonaws.com` for
  `sts:AssumeRole` and `sts:TagSession`, and a Terraform-managed Pod Identity
  association for this namespace/service account. Add that role to the auth
  module's `signer_principal_arns`; do not grant publisher/admin permissions.
- A network-policy-capable CNI and the intended caller label `app: fhir-host-app`
  in the same namespace. ClusterIP alone is not access control. Configure TLS
  between services (for example your service mesh) before handling real data;
  the template's HTTP listener expects that separate transport layer.
- Outbound connectivity to Epic, AWS KMS and the Pod Identity credential endpoint.
  No blanket egress-deny policy is provided because these destinations depend
  on the cluster network design. Restrict node instance-role access as well.

Use Terraform in the private deployment repo to manage these environment-specific
resources and deploy the Kubernetes template with your established workflow.
No long-lived AWS access key is needed in the pod. Probe success does not prove
that an Epic registration is enabled or has access to every resource type.

References: [FastAPI containers](https://fastapi.tiangolo.com/deployment/docker/),
[EKS Pod Identity SDK support](https://docs.aws.amazon.com/eks/latest/userguide/pod-id-minimum-sdk.html).
