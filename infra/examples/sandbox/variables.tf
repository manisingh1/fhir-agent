variable "account_id" {
  type        = string
  description = "Intended AWS account; provider refuses any other account."
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "Supply a 12-digit AWS account ID."
  }
}
variable "region" {
  type    = string
  default = "us-east-2"
}
variable "name" {
  type    = string
  default = "fhir-agent-sandbox"
}
variable "admin_principal_arns" { type = set(string) }
variable "signer_principal_arns" {
  type    = set(string)
  default = []
}
variable "publisher_principal_arns" { type = set(string) }
variable "key_versions" {
  type    = set(string)
  default = ["v1"]
}
variable "active_key_version" {
  type    = string
  default = "v1"
}
variable "published_key_versions" {
  type    = set(string)
  default = null
}
