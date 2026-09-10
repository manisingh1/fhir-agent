variable "name" {
  description = "Unique lowercase project/environment prefix."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.name))
    error_message = "Use 3-31 lowercase letters, numbers, or hyphens, starting with a letter."
  }
}

variable "admin_principal_arns" {
  description = "Existing IAM users/roles administering this key; include the Terraform deployment identity."
  type        = set(string)
  validation {
    condition     = length(var.admin_principal_arns) > 0
    error_message = "Specify at least one key administrator."
  }
}

variable "signer_principal_arns" {
  description = "Existing IAM users/roles allowed to sign Epic assertions. No roles or credentials are created."
  type        = set(string)
  default     = []
}

variable "publisher_principal_arns" {
  description = "Existing IAM users/roles allowed to retrieve public keys and publish this JWKS object."
  type        = set(string)
  validation {
    condition     = length(var.publisher_principal_arns) > 0
    error_message = "Specify at least one JWKS publisher."
  }
}

variable "key_versions" {
  description = "Persistent key labels. Add a version before rotation; never rename/remove a key casually."
  type        = set(string)
  default     = ["v1"]
  validation {
    condition     = length(var.key_versions) > 0 && length(var.key_versions) <= 10 && alltrue([for v in var.key_versions : can(regex("^[a-z0-9][a-z0-9-]{0,19}$", v))])
    error_message = "Use 1-10 short lowercase key version labels."
  }
}

variable "active_key_version" {
  description = "Version used for new assertions; publish its public key before switching the application."
  type        = string
  default     = "v1"
  validation {
    condition     = contains(var.key_versions, var.active_key_version)
    error_message = "The active version must be present in key_versions."
  }
}

variable "published_key_versions" {
  description = "Versions in JWKS; null publishes all retained keys. Exclude retired versions only after overlap."
  type        = set(string)
  default     = null
  validation {
    condition = var.published_key_versions == null ? true : (
      length(var.published_key_versions) > 0 &&
      length(setsubtract(var.published_key_versions, var.key_versions)) == 0 &&
      contains(var.published_key_versions, var.active_key_version)
    )
    error_message = "Published versions must be retained keys and include the active version."
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}
