terraform {
  required_version = ">= 1.11, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
  # Initially local; migrate after creation using backend_override.tf.example.
}

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.account_id]
}

data "aws_partition" "current" {}

locals {
  root_arn = "arn:${data.aws_partition.current.partition}:iam::${var.account_id}:root"
}

resource "aws_s3_bucket" "state" {
  bucket        = var.bucket_name
  force_destroy = false
  tags          = { Project = "fhir-agent", Purpose = "terraform-state", ManagedBy = "Terraform" }
  lifecycle {
    prevent_destroy = true
    precondition {
      condition = length(var.admin_principal_arns) > 0 && alltrue([for arn in var.admin_principal_arns : can(regex(
        "^arn:${data.aws_partition.current.partition}:iam::${var.account_id}:(user|role)/[A-Za-z0-9+=,.@_/-]+$", arn
      ))])
      error_message = "Supply existing same-account IAM user/role ARNs for state administration."
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule { object_ownership = "BucketOwnerEnforced" }
}
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "StateAdministrators"
        Effect    = "Allow"
        Principal = { AWS = sort(tolist(var.admin_principal_arns)) }
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
      },
      {
        Sid       = "RestrictStateAccess"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
        Condition = { ArnNotEquals = { "aws:PrincipalArn" = concat([local.root_arn], sort(tolist(var.admin_principal_arns))) } }
      },
      {
        Sid       = "RequireTLS"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      }
    ]
  })
}
