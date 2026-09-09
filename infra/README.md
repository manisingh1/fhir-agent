# Epic authentication infrastructure

Keep actual deployment configuration in a private deployment repository and
credentials, state, and plans out of Git. The bootstrap template creates protected
Terraform state storage; review a plan before applying it.

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
