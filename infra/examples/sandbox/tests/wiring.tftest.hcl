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
  account_id               = "123456789012"
  region                   = "us-east-2"
  key_versions             = ["v1", "v2"]
  active_key_version       = "v2"
  published_key_versions   = ["v2"]
  name                     = "fhir-test-sandbox"
  admin_principal_arns     = ["arn:aws:iam::123456789012:role/Deployer"]
  signer_principal_arns    = ["arn:aws:iam::123456789012:role/FhirRuntime"]
  publisher_principal_arns = ["arn:aws:iam::123456789012:user/Publisher"]
}

run "sandbox_outputs" {
  command = apply
  assert {
    condition     = output.client_config.EPIC_KEY_ID == "fhir-test-sandbox-v2" && output.client_config.AWS_REGION == "us-east-2"
    error_message = "Sandbox must forward the name, active key version, and region."
  }
  assert {
    condition     = length(output.publisher_config.keys) == 1 && output.publisher_config.keys[0].kid == "fhir-test-sandbox-v2" && output.publisher_config.keys[0].arn == output.client_config.EPIC_KMS_KEY_ID
    error_message = "Publisher output must select the configured public version and match the active client key."
  }
  assert {
    condition     = output.publisher_config.account_id == "123456789012" && output.publisher_config.jwks_url == output.jwks_url && output.jwks_url == "https://example.cloudfront.net/jwks.json"
    error_message = "Sandbox must expose the module account and public endpoint consistently."
  }
  assert {
    condition     = output.signing_policy_arn == "arn:aws:iam::123456789012:policy/test-policy" && output.publishing_policy_arn == "arn:aws:iam::123456789012:policy/test-policy"
    error_message = "Sandbox must expose the managed policies."
  }
}
