output "ingestion_role_arn" {
  value = aws_iam_role.ingestion.arn
}

output "query_role_arn" {
  value = aws_iam_role.query.arn
}

output "worker_role_arn" {
  value = aws_iam_role.worker.arn
}

output "migration_role_arn" {
  value = aws_iam_role.migration.arn
}

output "role_names" {
  value = {
    ingestion = aws_iam_role.ingestion.name
    query     = aws_iam_role.query.name
    worker    = aws_iam_role.worker.name
    migration = aws_iam_role.migration.name
  }
}
