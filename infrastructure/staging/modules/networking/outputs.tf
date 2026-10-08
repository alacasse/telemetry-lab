output "vpc_id" {
  value = aws_vpc.this.id
}

output "public_subnet_ids" {
  value = [for zone in var.availability_zones : aws_subnet.public[zone].id]
}

output "app_private_subnet_ids" {
  value = [for zone in var.availability_zones : aws_subnet.app_private[zone].id]
}

output "data_private_subnet_ids" {
  value = [for zone in var.availability_zones : aws_subnet.data_private[zone].id]
}

output "database_security_group_id" {
  value = aws_security_group.database.id
}

output "lambda_security_group_id" {
  value = aws_security_group.lambda_vpc.id
}

output "vpc_endpoint_security_group_id" {
  value = aws_security_group.vpc_endpoints.id
}

output "secrets_manager_vpc_endpoint_id" {
  value = aws_vpc_endpoint.secrets_manager.id
}
