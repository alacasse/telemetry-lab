output "instance_identifier" {
  value = aws_db_instance.this.id
}

output "database_name" {
  value = aws_db_instance.this.db_name
}

output "db_username" {
  value = aws_db_instance.this.username
}

output "address" {
  value = aws_db_instance.this.address
}

output "port" {
  value = aws_db_instance.this.port
}

output "arn" {
  value = aws_db_instance.this.arn
}

output "subnet_group_name" {
  value = aws_db_subnet_group.this.name
}
