locals {
  public_subnet_config = {
    for index, zone in var.availability_zones : zone => {
      cidr = cidrsubnet(var.vpc_cidr, 8, index)
    }
  }

  app_private_subnet_config = {
    for index, zone in var.availability_zones : zone => {
      cidr = cidrsubnet(var.vpc_cidr, 8, index + 10)
    }
  }

  data_private_subnet_config = {
    for index, zone in var.availability_zones : zone => {
      cidr = cidrsubnet(var.vpc_cidr, 8, index + 20)
    }
  }
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-vpc"
  })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-igw"
  })
}

resource "aws_subnet" "public" {
  for_each = local.public_subnet_config

  vpc_id                  = aws_vpc.this.id
  cidr_block              = each.value.cidr
  availability_zone       = each.key
  map_public_ip_on_launch = true

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-${each.key}-public"
    Tier = "public"
  })
}

resource "aws_subnet" "app_private" {
  for_each = local.app_private_subnet_config

  vpc_id            = aws_vpc.this.id
  cidr_block        = each.value.cidr
  availability_zone = each.key

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-${each.key}-app-private"
    Tier = "app-private"
  })
}

resource "aws_subnet" "data_private" {
  for_each = local.data_private_subnet_config

  vpc_id            = aws_vpc.this.id
  cidr_block        = each.value.cidr
  availability_zone = each.key

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-${each.key}-data-private"
    Tier = "data-private"
  })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-public-rt"
  })
}

resource "aws_route_table" "app_private" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-app-private-rt"
  })
}

resource "aws_route_table" "data_private" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-data-private-rt"
  })
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "app_private" {
  for_each = aws_subnet.app_private

  subnet_id      = each.value.id
  route_table_id = aws_route_table.app_private.id
}

resource "aws_route_table_association" "data_private" {
  for_each = aws_subnet.data_private

  subnet_id      = each.value.id
  route_table_id = aws_route_table.data_private.id
}

resource "aws_security_group" "lambda_vpc" {
  name        = "${var.name_prefix}-lambda-sg"
  description = "Application Lambda access for Telemetry Lab staging"
  vpc_id      = aws_vpc.this.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-lambda-sg"
  })
}

resource "aws_security_group" "database" {
  name        = "${var.name_prefix}-db-sg"
  description = "PostgreSQL access for Telemetry Lab staging"
  vpc_id      = aws_vpc.this.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-db-sg"
  })
}

resource "aws_security_group" "vpc_endpoints" {
  name        = "${var.name_prefix}-vpce-sg"
  description = "Secrets Manager VPC endpoint access"
  vpc_id      = aws_vpc.this.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-vpce-sg"
  })
}

resource "aws_vpc_security_group_ingress_rule" "lambda_to_database" {
  security_group_id            = aws_security_group.database.id
  referenced_security_group_id = aws_security_group.lambda_vpc.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  description                  = "PostgreSQL from application Lambdas"
}

resource "aws_vpc_security_group_ingress_rule" "lambda_to_secrets_endpoint" {
  security_group_id            = aws_security_group.vpc_endpoints.id
  referenced_security_group_id = aws_security_group.lambda_vpc.id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
  description                  = "HTTPS from application Lambdas to Secrets Manager endpoint"
}

resource "aws_vpc_endpoint" "secrets_manager" {
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.aws_region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [for zone in var.availability_zones : aws_subnet.app_private[zone].id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-secretsmanager-vpce"
  })
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.app_private.id, aws_route_table.data_private.id]

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-s3-vpce"
  })
}
