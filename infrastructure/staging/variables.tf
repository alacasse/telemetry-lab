variable "project_name" {
  description = "Project slug used for resource naming."
  type        = string
  default     = "telemetry-lab"
}

variable "environment" {
  description = "Deployment environment identifier."
  type        = string
  default     = "staging"

  validation {
    condition     = var.environment == "staging"
    error_message = "Only the staging environment is supported."
  }
}

variable "aws_region" {
  description = "AWS region for deployed resources."
  type        = string
  default     = "ca-central-1"
}

variable "skip_aws_credentials_validation" {
  description = "Skip AWS credential checks for offline Terraform validation and planning."
  type        = bool
  default     = false
}

variable "availability_zones" {
  description = "Availability zones reserved for public, app-private, and data-private subnets."
  type        = list(string)
  default     = ["ca-central-1a", "ca-central-1b"]
}

variable "vpc_cidr" {
  description = "CIDR block reserved for the staging VPC."
  type        = string
  default     = "10.20.0.0/16"
}

variable "db_name" {
  description = "Initial database name for the application."
  type        = string
  default     = "telemetry_lab"
}

variable "db_username" {
  description = "Primary database user name."
  type        = string
  default     = "telemetry_lab"
}

variable "db_instance_class" {
  description = "RDS instance class for the staging database."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "Allocated PostgreSQL storage in GiB."
  type        = number
  default     = 20
}

variable "db_backup_retention_period" {
  description = "Automated backup retention window in days."
  type        = number
  default     = 7
}

variable "skip_final_snapshot" {
  description = "Skip the final RDS snapshot during destroy."
  type        = bool
  default     = false
}

variable "queue_name" {
  description = "Base queue name for telemetry ingestion."
  type        = string
  default     = "telemetry-lab-telemetry"
}

variable "queue_visibility_timeout_seconds" {
  description = "Telemetry queue visibility timeout in seconds."
  type        = number
  default     = 360
}

variable "queue_message_retention_seconds" {
  description = "Telemetry queue retention period in seconds."
  type        = number
  default     = 86400
}

variable "dlq_message_retention_seconds" {
  description = "Dead-letter queue retention period in seconds."
  type        = number
  default     = 604800
}

variable "queue_max_receive_count" {
  description = "Maximum receive attempts before a message is moved to the DLQ."
  type        = number
  default     = 3
}

variable "lambda_artifact_bucket" {
  description = "S3 bucket that stores packaged Lambda artifacts."
  type        = string
  default     = "telemetry-lab-artifacts"
}

variable "lambda_artifact_revision" {
  description = "Artifact revision prefix, usually the commit SHA used for packaged bundles."
  type        = string
  default     = "latest"
}

variable "log_level" {
  description = "Runtime log level for Lambda functions."
  type        = string
  default     = "INFO"
}

variable "enable_xray" {
  description = "Enable X-Ray tracing in supported Lambda runtimes."
  type        = bool
  default     = true
}

variable "create_kms_key" {
  description = "Create a dedicated KMS key for staging resources."
  type        = bool
  default     = false
}

variable "database_secret_name" {
  description = "Secrets Manager secret name for the staging database payload."
  type        = string
  default     = "telemetry-lab/staging/database"
}

variable "staging_auth_secret_name" {
  description = "Secrets Manager secret name for the staging access token."
  type        = string
  default     = "telemetry-lab/staging/auth-token"
}

variable "budget_actual_threshold_usd" {
  description = "Actual monthly spend threshold in USD for the staging budget alert."
  type        = number
  default     = 5
}

variable "budget_forecast_threshold_usd" {
  description = "Forecast monthly spend threshold in USD for the staging budget alert."
  type        = number
  default     = 10
}

variable "budget_notification_emails" {
  description = "Email recipients notified by AWS Budgets. Leave empty to disable budget alerts."
  type        = list(string)
  default     = []
}

variable "budget_notification_sns_topic_arns" {
  description = "SNS topic ARNs notified by AWS Budgets. Leave empty to disable budget alerts."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Additional tags to merge into every named component."
  type        = map(string)
  default     = {}
}
