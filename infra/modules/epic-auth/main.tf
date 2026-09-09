data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  root_arn       = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"
  object_key     = "jwks.json"
  origin_id      = "public-jwks"
  all_principals = setunion(var.admin_principal_arns, var.signer_principal_arns, var.publisher_principal_arns)
  signers        = { for arn in var.signer_principal_arns : arn => basename(arn) }
  publishers     = { for arn in var.publisher_principal_arns : arn => basename(arn) }
}

resource "aws_kms_key" "signing" {
  for_each                 = var.key_versions
  description              = "${var.name} Epic backend RS384 signing (${each.key})"
  customer_master_key_spec = "RSA_2048"
  key_usage                = "SIGN_VERIFY"
  deletion_window_in_days  = 30
  multi_region             = false
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Sid       = "AccountRecovery"
        Effect    = "Allow"
        Principal = { AWS = local.root_arn }
        Action    = "kms:*"
        Resource  = "*"
        # Do not delegate access to every IAM identity in this account.
        Condition = { ArnEquals = { "aws:PrincipalArn" = local.root_arn } }
      },
      {
        Sid       = "KeyAdministrators"
        Effect    = "Allow"
        Principal = { AWS = sort(tolist(var.admin_principal_arns)) }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "PublicKeyPublishers"
        Effect    = "Allow"
        Principal = { AWS = sort(tolist(var.publisher_principal_arns)) }
        Action    = ["kms:GetPublicKey", "kms:DescribeKey"]
        Resource  = "*"
      }
      ], length(var.signer_principal_arns) == 0 ? [] : [{
        Sid       = "AssertionSigners"
        Effect    = "Allow"
        Principal = { AWS = sort(tolist(var.signer_principal_arns)) }
        Action    = ["kms:Sign"]
        Resource  = "*"
        Condition = { StringEquals = { "kms:SigningAlgorithm" = "RSASSA_PKCS1_V1_5_SHA_384" } }
    }])
  })
  tags = merge(var.tags, { Name = "${var.name}-${each.key}" })
  lifecycle {
    prevent_destroy = true
    precondition {
      condition = alltrue([for arn in local.all_principals : can(regex(
        "^arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:(user|role)/[A-Za-z0-9+=,.@_/-]+$", arn
      ))])
      error_message = "Use existing IAM user/role ARNs in the deployment account, not STS session ARNs or wildcards."
    }
  }
}

resource "aws_kms_alias" "signing" {
  for_each      = aws_kms_key.signing
  name          = "alias/${var.name}-${each.key}"
  target_key_id = each.value.key_id
}

resource "aws_s3_bucket" "jwks" {
  bucket        = "${var.name}-jwks-${data.aws_caller_identity.current.account_id}"
  force_destroy = false
  tags          = var.tags
  lifecycle { prevent_destroy = true }
}

resource "aws_s3_bucket_public_access_block" "jwks" {
  bucket                  = aws_s3_bucket.jwks.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "jwks" {
  bucket = aws_s3_bucket.jwks.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_versioning" "jwks" {
  bucket = aws_s3_bucket.jwks.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "jwks" {
  bucket = aws_s3_bucket.jwks.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_cloudfront_origin_access_control" "jwks" {
  name                              = "${var.name}-jwks"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_cache_policy" "jwks" {
  name        = "${var.name}-jwks"
  min_ttl     = 0
  default_ttl = 300
  max_ttl     = 300
  parameters_in_cache_key_and_forwarded_to_origin {
    cookies_config { cookie_behavior = "none" }
    headers_config { header_behavior = "none" }
    query_strings_config { query_string_behavior = "none" }
  }
}

resource "aws_cloudfront_distribution" "jwks" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "${var.name}: public verification keys only"
  price_class         = "PriceClass_100"
  wait_for_deployment = true
  origin {
    domain_name              = aws_s3_bucket.jwks.bucket_regional_domain_name
    origin_id                = local.origin_id
    origin_access_control_id = aws_cloudfront_origin_access_control.jwks.id
    s3_origin_config { origin_access_identity = "" }
  }
  default_cache_behavior {
    target_origin_id       = local.origin_id
    viewer_protocol_policy = "https-only"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = aws_cloudfront_cache_policy.jwks.id
    compress               = true
  }
  restrictions {
    geo_restriction { restriction_type = "none" }
  }
  viewer_certificate { cloudfront_default_certificate = true }
  custom_error_response {
    error_code            = 403
    error_caching_min_ttl = 0
  }
  custom_error_response {
    error_code            = 404
    error_caching_min_ttl = 0
  }
  tags = var.tags
}

resource "aws_s3_bucket_policy" "jwks" {
  bucket = aws_s3_bucket.jwks.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "CloudFrontReadPublicKeyOnly"
        Effect    = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.jwks.arn}/${local.object_key}"
        Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.jwks.arn } }
      },
      {
        Sid       = "RequireTLS"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.jwks.arn, "${aws_s3_bucket.jwks.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "RestrictPublicKeyChanges"
        Effect    = "Deny"
        Principal = "*"
        Action    = ["s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion"]
        Resource  = "${aws_s3_bucket.jwks.arn}/*"
        Condition = { ArnNotEquals = { "aws:PrincipalArn" = concat([local.root_arn], sort(tolist(setunion(var.admin_principal_arns, var.publisher_principal_arns)))) } }
      }
    ]
  })
}
