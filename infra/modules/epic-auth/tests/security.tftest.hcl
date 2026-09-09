# Entire provider is mocked: these tests do not authenticate to AWS or deploy.
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_data "aws_region" {
    defaults = { region = "us-east-2" }
  }
  mock_resource "aws_iam_policy" {
    defaults = { arn = "arn:aws:iam::123456789012:policy/test-policy" }
  }
  mock_resource "aws_kms_key" {
    defaults = {
      arn    = "arn:aws:kms:us-east-2:123456789012:key/12345678-1234-1234-1234-123456789012"
      key_id = "12345678-1234-1234-1234-123456789012"
    }
  }
  mock_resource "aws_s3_bucket" {
    defaults = {
      arn                         = "arn:aws:s3:::fhir-test-bucket"
      id                          = "fhir-test-bucket"
      bucket_regional_domain_name = "fhir-test-bucket.s3.us-east-2.amazonaws.com"
    }
  }
  mock_resource "aws_cloudfront_distribution" {
    defaults = {
      arn         = "arn:aws:cloudfront::123456789012:distribution/EXAMPLE"
      id          = "EXAMPLE"
      domain_name = "example.cloudfront.net"
    }
  }
}

variables {
  name                     = "fhir-test-sandbox"
  admin_principal_arns     = ["arn:aws:iam::123456789012:role/Deployer"]
  signer_principal_arns    = ["arn:aws:iam::123456789012:role/FhirRuntime"]
  publisher_principal_arns = ["arn:aws:iam::123456789012:user/Publisher"]
}

run "private_origin_and_separate_permissions" {
  command = apply
  assert {
    condition     = aws_kms_key.signing["v1"].key_usage == "SIGN_VERIFY" && aws_kms_key.signing["v1"].customer_master_key_spec == "RSA_2048"
    error_message = "Keys must be asymmetric signing keys."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.jwks.block_public_acls && aws_s3_bucket_public_access_block.jwks.block_public_policy && aws_s3_bucket_public_access_block.jwks.ignore_public_acls && aws_s3_bucket_public_access_block.jwks.restrict_public_buckets
    error_message = "All S3 public access blocks must remain enabled."
  }
  assert {
    condition     = aws_cloudfront_origin_access_control.jwks.signing_behavior == "always" && aws_cloudfront_distribution.jwks.default_cache_behavior[0].viewer_protocol_policy == "https-only"
    error_message = "Use signed origin requests and HTTPS viewers."
  }
  assert {
    condition     = jsondecode(aws_s3_bucket_policy.jwks.policy).Statement[0].Resource == "${aws_s3_bucket.jwks.arn}/jwks.json" && jsondecode(aws_s3_bucket_policy.jwks.policy).Statement[0].Condition.StringEquals["AWS:SourceArn"] == aws_cloudfront_distribution.jwks.arn
    error_message = "CloudFront may read only JWKS and must be restricted to this distribution."
  }
  assert {
    condition     = jsondecode(aws_iam_policy.signing.policy).Statement[0].Action == ["kms:Sign"] && jsondecode(aws_iam_policy.signing.policy).Statement[0].Resource == [aws_kms_key.signing["v1"].arn]
    error_message = "Application permissions must be scoped to signing with this key."
  }
  assert {
    condition     = !strcontains(aws_iam_policy.publishing.policy, "kms:Sign") && !strcontains(aws_iam_policy.publishing.policy, "s3:DeleteObject")
    error_message = "Publisher must not gain signing or deletion permissions."
  }
  assert {
    condition     = length(aws_iam_role_policy_attachment.signing) == 1 && length(aws_iam_user_policy_attachment.publishing) == 1 && length(aws_iam_user_policy_attachment.signing) == 0
    error_message = "Attach each policy only to the configured identity."
  }
  assert {
    condition     = jsondecode(aws_kms_key.signing["v1"].policy).Statement[0].Condition.ArnEquals["aws:PrincipalArn"] == "arn:aws:iam::123456789012:root"
    error_message = "Account recovery must not broadly delegate KMS access to all IAM identities."
  }
}

run "reject_cross_account_identity" {
  command = plan
  variables {
    signer_principal_arns = ["arn:aws:iam::999999999999:role/OtherAccount"]
  }
  expect_failures = [aws_kms_key.signing]
}

run "reject_unknown_active_version" {
  command = plan
  variables {
    active_key_version = "missing"
  }
  expect_failures = [var.active_key_version]
}

run "rotation_keeps_both_keys" {
  command = apply
  variables {
    key_versions       = ["v1", "v2"]
    active_key_version = "v2"
  }
  assert {
    condition     = length(output.publisher_config.keys) == 2 && output.client_config.EPIC_KEY_ID == "fhir-test-sandbox-v2"
    error_message = "Rotation must retain the prior public key while selecting the new signer."
  }
}

run "retirement_preserves_kms_key" {
  command = apply
  variables {
    key_versions           = ["v1", "v2"]
    active_key_version     = "v2"
    published_key_versions = ["v2"]
  }
  assert {
    condition     = length(aws_kms_key.signing) == 2 && length(output.publisher_config.keys) == 1 && output.publisher_config.keys[0].kid == "fhir-test-sandbox-v2"
    error_message = "Retiring a public key must not delete its KMS key."
  }
}
