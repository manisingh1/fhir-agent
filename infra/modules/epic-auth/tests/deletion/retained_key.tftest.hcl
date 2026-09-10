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

# This file intentionally fails on the second run. Run via scripts/test_key_retention.py.
run "retain_keys" {
  command = apply
  variables {
    key_versions       = ["v1", "v2"]
    active_key_version = "v2"
  }
}
run "remove_retained_key" {
  command = plan
  variables {
    key_versions       = ["v2"]
    active_key_version = "v2"
  }
}
