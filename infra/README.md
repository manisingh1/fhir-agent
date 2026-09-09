# Epic authentication infrastructure

Reusable public Terraform code for KMS signing keys and a public JWKS endpoint.
It does not deploy the FHIR application, configure Epic, or store patient data.
No Lambda is needed to serve a static public key set.

## Public code and private configuration

- `modules/epic-auth`: KMS, S3, CloudFront, and IAM module.
- `examples/sandbox`: root-module example with placeholders only.
- `bootstrap`: protected Terraform state storage template.

Put real deployment roots, account/role identifiers, backend settings, and CI
workflows in a separate private deployment repository. Consume this public module
at a reviewed full commit SHA, after it has been published:

```hcl
module "epic_auth" {
  source = "git::https://github.com/manisingh1/fhir-agent.git//infra/modules/epic-auth?ref=REVIEWED_COMMIT_SHA"
  # Supply inputs as shown in examples/sandbox/main.tf.
}
```

Replace the placeholder with a real commit, not a moving branch. Keep provider
lockfiles in deployment roots. A private repo must still not contain credentials,
private keys, Terraform state/plans, or patient data. Use existing AWS profiles,
SSO, or workload roles; do not embed access keys in provider/backend configuration.
This module does not create CI federation, users, access keys, or execution roles.

## Resources and permissions

- Versioned RSA-2048 `SIGN_VERIFY` KMS keys for RS384, a 30-day deletion window,
  and `prevent_destroy`. KMS creates and retains private material; Terraform never
  handles it. Descriptive aliases are provided, but clients use stable key ARNs.
- Dedicated AES256-encrypted, versioned S3 bucket, all public-access blocks enabled,
  ACLs disabled. Only public `jwks.json` is intended for this bucket.
- CloudFront HTTPS hostname with signed Origin Access Control, GET/HEAD only,
  five-minute caching, and S3 access limited to exactly `jwks.json` from that
  distribution. Other objects are not exposed through CloudFront.
- Managed policies attached to only the explicitly configured, existing,
  same-account IAM users/roles. STS session ARNs and wildcards are rejected.

`signer_principal_arns` get `kms:Sign` on this environment's retained keys,
restricted to RS384. `publisher_principal_arns` get public-key retrieval,
read/write of the JWKS object, prefix-limited listing, and this distribution's
invalidation permissions. Publisher permissions do not include signing or deletion.
Key administrators have `kms:*`; account root retains recovery access without
broadly delegating KMS permissions to every IAM identity.

Include the Terraform deployment identity in `admin_principal_arns`. Existing IAM
permissions still apply. Administrators can sign and change policies: separation
is not a boundary against administrators. The JWKS bucket denies object changes
outside the listed administrators/publishers and account root, but infrastructure
administrators can still modify its policy.

For a future Lambda **execution role** or ECS **task role**, add the role ARN to
`signer_principal_arns`. The module attaches the signing policy and updates the KMS
policy. The service assumes its role and supplies temporary credentials, avoiding
stored AWS keys. Service trust policies belong in the application stack. ECS SDK
calls use the task role, not the ECS task execution role. Signing allows the app
to create an assertion; Epic separately authorizes the resulting FHIR token.
Keep publishing and deployment permissions off the runtime role.

## Prerequisites

Terraform >= 1.11 and < 2; committed AWS provider lockfiles; an OpenSSL-based
Python >= 3.9 with `pip install -e '.[aws,test]'` in a virtual environment. The
deployment identity needs permissions to manage the declared resources and IAM
policy attachments; the module does not grant these deployment privileges.
This initial stack targets the standard AWS partition.

Use separate state, keys, hosting, and Epic registrations for production,
preferably in another account. Both the provider and configured S3 backend use
`allowed_account_ids` to reject the wrong account. Resource names must be unique.
KMS, S3, and CloudFront incur AWS charges; this is not a zero-cost deployment.

## 1. Bootstrap protected state

Prefer to copy these templates into the private deployment repo. Paths below
illustrate the layout here. Edit placeholders before running Terraform:

```sh
cp infra/bootstrap/terraform.tfvars.example infra/bootstrap/terraform.tfvars
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap plan -out=bootstrap.tfplan
terraform -chdir=infra/bootstrap apply bootstrap.tfplan
```

Include the deploying IAM identity in the state administrators. The state bucket
enforces TLS, encryption, versioning, blocked public access, and denies access
outside listed administrators and account root. It grants no state access to
the runtime or publisher. No state-version expiration is configured.

Bootstrap starts with **local state**. After creating its bucket, copy
`backend_override.tf.example` to `backend_override.tf` and
`backend.tfbackend.example` to `backend.tfbackend`, fill in the actual bucket,
region, and account, then migrate:

```sh
terraform -chdir=infra/bootstrap init -migrate-state -backend-config=backend.tfbackend
terraform -chdir=infra/bootstrap plan
```

Confirm migration and preserve a protected backup. Never commit state, backups,
saved plans, or local backend overrides. Native S3 locking (`use_lockfile`) avoids
a separate DynamoDB table. The bucket has `prevent_destroy` and `force_destroy=false`.

## 2. Deploy the authentication stack

In a private deployment repo, use the pinned Git module source above. For local
testing with these examples:

```sh
cp infra/examples/sandbox/terraform.tfvars.example infra/examples/sandbox/terraform.tfvars
cp infra/examples/sandbox/backend.tfbackend.example infra/examples/sandbox/backend.tfbackend
# Edit both files with your own account, existing identities, and state bucket.
terraform -chdir=infra/examples/sandbox init -backend-config=backend.tfbackend
terraform -chdir=infra/examples/sandbox plan -out=sandbox.tfplan
terraform -chdir=infra/examples/sandbox apply sandbox.tfplan
```

Review the plan before applying. CloudFront deployment can take several minutes.
The endpoint returns an error until JWKS is published. Terraform deliberately
does not own that object, preventing conflicts with the publishing script.
