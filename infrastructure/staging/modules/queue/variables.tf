variable "name_prefix" {
  type = string
}

variable "queue_name" {
  type = string
}

variable "visibility_timeout_seconds" {
  type = number
}

variable "message_retention_seconds" {
  type = number
}

variable "dead_letter_retention_seconds" {
  type = number
}

variable "max_receive_count" {
  type = number
}

variable "kms_key_id" {
  type    = string
  default = null
}

variable "tags" {
  type = map(string)
}
