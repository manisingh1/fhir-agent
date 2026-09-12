terraform {
  required_version = ">= 1.14, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
  backend "s3" {
    key          = "sandbox/epic-auth.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.account_id]
}

module "epic_auth" {
  source                   = "../../modules/epic-auth"
  name                     = var.name
  admin_principal_arns     = var.admin_principal_arns
  signer_principal_arns    = var.signer_principal_arns
  publisher_principal_arns = var.publisher_principal_arns
  key_versions             = var.key_versions
  active_key_version       = var.active_key_version
  published_key_versions   = var.published_key_versions
  tags                     = { Project = "fhir-agent", Environment = "sandbox", ManagedBy = "Terraform" }
}
