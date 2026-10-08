variable "name_prefix" {
  type = string
}

variable "ingestion_lambda_name" {
  type = string
}

variable "ingestion_lambda_invoke_arn" {
  type = string
}

variable "query_lambda_name" {
  type = string
}

variable "query_lambda_invoke_arn" {
  type = string
}

variable "tags" {
  type = map(string)
}
