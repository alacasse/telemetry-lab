output "queue_name" {
  value = aws_sqs_queue.telemetry.name
}

output "queue_url" {
  value = aws_sqs_queue.telemetry.url
}

output "queue_arn" {
  value = aws_sqs_queue.telemetry.arn
}

output "dead_letter_queue_name" {
  value = aws_sqs_queue.dead_letter.name
}

output "dead_letter_queue_url" {
  value = aws_sqs_queue.dead_letter.url
}

output "dead_letter_queue_arn" {
  value = aws_sqs_queue.dead_letter.arn
}
