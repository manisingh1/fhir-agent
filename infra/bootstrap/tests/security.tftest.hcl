mock_provider "aws" {
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::fhir-test-state", id = "fhir-test-state" }
  }
}
variables {
  account_id           = "123456789012"
  bucket_name          = "fhir-test-state"
  admin_principal_arns = ["arn:aws:iam::123456789012:role/Deployer"]
}
run "protected_state" {
  command = apply
  assert {
    condition     = aws_s3_bucket_public_access_block.state.block_public_acls && aws_s3_bucket_public_access_block.state.block_public_policy && aws_s3_bucket_public_access_block.state.ignore_public_acls && aws_s3_bucket_public_access_block.state.restrict_public_buckets
    error_message = "State must never be public."
  }
  assert {
    condition     = aws_s3_bucket_versioning.state.versioning_configuration[0].status == "Enabled" && one(aws_s3_bucket_server_side_encryption_configuration.state.rule).apply_server_side_encryption_by_default[0].sse_algorithm == "AES256"
    error_message = "State must be encrypted and versioned."
  }
  assert {
    condition     = jsondecode(aws_s3_bucket_policy.state.policy).Statement[1].Effect == "Deny" && length(jsondecode(aws_s3_bucket_policy.state.policy).Statement[1].Condition.ArnNotEquals["aws:PrincipalArn"]) == 2
    error_message = "State access must be restricted to the administrator and account recovery."
  }
}

run "state_tls_boundary" {
  command = apply
  assert {
    condition     = jsondecode(aws_s3_bucket_policy.state.policy).Statement[2].Effect == "Deny" && jsondecode(aws_s3_bucket_policy.state.policy).Statement[2].Principal == "*" && jsondecode(aws_s3_bucket_policy.state.policy).Statement[2].Action == "s3:*" && jsondecode(aws_s3_bucket_policy.state.policy).Statement[2].Condition.Bool["aws:SecureTransport"] == "false" && toset(jsondecode(aws_s3_bucket_policy.state.policy).Statement[2].Resource) == toset([aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"])
    error_message = "State bucket must deny all non-TLS requests."
  }
}

run "reject_empty_admins" {
  command = plan
  variables {
    admin_principal_arns = []
  }
  expect_failures = [aws_s3_bucket.state]
}

run "reject_wildcard_admin" {
  command = plan
  variables {
    admin_principal_arns = ["*"]
  }
  expect_failures = [aws_s3_bucket.state]
}

run "reject_sts_admin" {
  command = plan
  variables {
    admin_principal_arns = ["arn:aws:sts::123456789012:assumed-role/Deployer/session"]
  }
  expect_failures = [aws_s3_bucket.state]
}
