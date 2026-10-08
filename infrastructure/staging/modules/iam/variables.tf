variable "name_prefix" {
  type = string
}

variable "queue_arn" {
  type = string
}

variable "database_secret_arn" {
  type = string
}

variable "staging_auth_secret_arn" {
  type = string
}

variable "kms_key_arn" {
  type    = string
  default = null
}

variable "tags" {
  type = map(string)
}
