variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "name_prefix" {
  type = string
}

variable "create_kms_key" {
  type = bool
}

variable "database_secret_name" {
  type = string
}

variable "staging_auth_secret_name" {
  type = string
}

variable "tags" {
  type = map(string)
}
