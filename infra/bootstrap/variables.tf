variable "account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "Supply a 12-digit AWS account ID."
  }
}
variable "region" {
  type    = string
  default = "us-east-2"
}
variable "bucket_name" {
  type        = string
  description = "Globally unique private state bucket name."
}
variable "admin_principal_arns" {
  type        = set(string)
  description = "Only these existing IAM identities and account root may access state. Include the deploying identity."
}
