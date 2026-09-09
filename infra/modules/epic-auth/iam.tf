resource "aws_iam_policy" "signing" {
  name        = "${var.name}-sign"
  description = "Sign Epic assertions with this environment's KMS keys only"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = ["kms:Sign"]
      Resource  = [for key in aws_kms_key.signing : key.arn]
      Condition = { StringEquals = { "kms:SigningAlgorithm" = "RSASSA_PKCS1_V1_5_SHA_384" } }
    }]
  })
  tags = var.tags
}

resource "aws_iam_policy" "publishing" {
  name        = "${var.name}-publish-jwks"
  description = "Retrieve public keys and publish only this environment's JWKS"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["kms:GetPublicKey", "kms:DescribeKey"]
        Resource = [for key in aws_kms_key.signing : key.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.jwks.arn}/${local.object_key}"
      },
      {
        Effect    = "Allow"
        Action    = ["s3:ListBucket"]
        Resource  = aws_s3_bucket.jwks.arn
        Condition = { StringEquals = { "s3:prefix" = local.object_key } }
      },
      {
        Effect   = "Allow"
        Action   = ["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"]
        Resource = aws_cloudfront_distribution.jwks.arn
      }
    ]
  })
  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "signing" {
  for_each   = { for arn, name in local.signers : arn => name if strcontains(arn, ":role/") }
  role       = each.value
  policy_arn = aws_iam_policy.signing.arn
}

resource "aws_iam_user_policy_attachment" "signing" {
  for_each   = { for arn, name in local.signers : arn => name if strcontains(arn, ":user/") }
  user       = each.value
  policy_arn = aws_iam_policy.signing.arn
}

resource "aws_iam_role_policy_attachment" "publishing" {
  for_each   = { for arn, name in local.publishers : arn => name if strcontains(arn, ":role/") }
  role       = each.value
  policy_arn = aws_iam_policy.publishing.arn
}

resource "aws_iam_user_policy_attachment" "publishing" {
  for_each   = { for arn, name in local.publishers : arn => name if strcontains(arn, ":user/") }
  user       = each.value
  policy_arn = aws_iam_policy.publishing.arn
}
