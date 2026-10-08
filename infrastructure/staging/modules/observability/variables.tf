variable "name_prefix" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "function_names" {
  type = map(string)
}

variable "api_id" {
  type = string
}

variable "queue_name" {
  type = string
}

variable "dead_letter_queue_name" {
  type = string
}

variable "database_identifier" {
  type = string
}

variable "tags" {
  type = map(string)
}
