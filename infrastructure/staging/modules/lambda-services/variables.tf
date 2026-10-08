variable "name_prefix" {
  type = string
}

variable "artifact_bucket" {
  type = string
}

variable "artifact_keys" {
  type = map(string)
}

variable "ingestion_role_arn" {
  type = string
}

variable "query_role_arn" {
  type = string
}

variable "worker_role_arn" {
  type = string
}

variable "migration_role_arn" {
  type = string
}

variable "queue_name" {
  type = string
}

variable "queue_url" {
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

variable "private_subnet_ids" {
  type = list(string)
}

variable "security_group_ids" {
  type = list(string)
}

variable "aws_region" {
  type = string
}

variable "log_level" {
  type = string
}

variable "enable_xray" {
  type = bool
}

variable "release_revision" {
  type = string
}

variable "tags" {
  type = map(string)
}
