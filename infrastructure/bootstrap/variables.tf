variable "project_name" {
  type    = string
  default = "telemetry-lab"
}

variable "environment" {
  type    = string
  default = "staging"

  validation {
    condition     = var.environment == "staging"
    error_message = "Bootstrap resources are only provisioned for staging."
  }
}

variable "aws_region" {
  type    = string
  default = "ca-central-1"
}

variable "state_bucket_name" {
  type    = string
  default = "telemetry-lab-tf-state"
}

variable "artifact_bucket_name" {
  type    = string
  default = "telemetry-lab-artifacts"
}

variable "lock_table_name" {
  type    = string
  default = "telemetry-lab-tf-locks"
}

variable "create_kms_key" {
  type    = bool
  default = false
}

variable "gitlab_oidc_provider_arn" {
  type    = string
  default = ""
}

variable "gitlab_project_path" {
  type    = string
  default = ""
}

variable "gitlab_default_branch" {
  type    = string
  default = "main"
}

variable "tags" {
  type    = map(string)
  default = {}
}
