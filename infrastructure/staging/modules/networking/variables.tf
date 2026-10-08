variable "name_prefix" {
  type = string
}

variable "vpc_cidr" {
  type = string
}

variable "availability_zones" {
  type = list(string)
}

variable "aws_region" {
  type = string
}

variable "tags" {
  type = map(string)
}
