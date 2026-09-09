output "jwks_url" { value = "https://${aws_cloudfront_distribution.jwks.domain_name}/${local.object_key}" }
output "signing_policy_arn" { value = aws_iam_policy.signing.arn }
output "publishing_policy_arn" { value = aws_iam_policy.publishing.arn }
output "publisher_config" {
  description = "Non-secret manifest for scripts/publish_jwks.py; keep environment-specific outputs local."
  value = {
    account_id      = data.aws_caller_identity.current.account_id
    region          = data.aws_region.current.region
    bucket          = aws_s3_bucket.jwks.id
    object_key      = local.object_key
    distribution_id = aws_cloudfront_distribution.jwks.id
    jwks_url        = "https://${aws_cloudfront_distribution.jwks.domain_name}/${local.object_key}"
    keys = [for version, key in aws_kms_key.signing : {
      arn = key.arn
      kid = "${var.name}-${version}"
    } if var.published_key_versions == null ? true : contains(var.published_key_versions, version)]
  }
}
output "client_config" {
  description = "Non-secret settings; add your Epic non-production client ID separately."
  value = {
    AWS_REGION      = data.aws_region.current.region
    EPIC_KMS_KEY_ID = aws_kms_key.signing[var.active_key_version].arn
    EPIC_KEY_ID     = "${var.name}-${var.active_key_version}"
  }
}
