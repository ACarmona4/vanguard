terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 7.0"
    }
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 8.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6, < 4.0"
    }
  }
}

variable "enable_aws" {
  description = "Create the disposable AWS inventory lab. Disabled by default."
  type        = bool
  default     = false
}

variable "aws_region" {
  description = "AWS Academy region used by the disposable lab."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = var.aws_region == "us-east-1"
    error_message = "This AWS Academy lab is intentionally restricted to us-east-1."
  }
}

provider "aws" {
  region = var.aws_region

  skip_credentials_validation = !var.enable_aws
  skip_metadata_api_check     = !var.enable_aws
  skip_requesting_account_id  = !var.enable_aws

  default_tags {
    tags = {
      Project     = "vanguard-inventory"
      Environment = "dummy"
      ManagedBy   = "terraform"
      Disposable  = "true"
    }
  }
}

data "aws_availability_zones" "available" {
  count = var.enable_aws ? 1 : 0

  state = "available"
}

data "aws_ami" "amazon_linux" {
  count = var.enable_aws ? 1 : 0

  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_vpc" "dummy" {
  count = var.enable_aws ? 1 : 0

  cidr_block           = "10.20.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "vanguard-dummy-vpc" }
}

resource "aws_subnet" "dummy" {
  count = var.enable_aws ? 1 : 0

  vpc_id                  = aws_vpc.dummy[0].id
  cidr_block              = cidrsubnet(aws_vpc.dummy[0].cidr_block, 8, 1)
  availability_zone       = data.aws_availability_zones.available[0].names[0]
  map_public_ip_on_launch = false

  tags = { Name = "vanguard-dummy-subnet" }
}

resource "aws_security_group" "vm" {
  count = var.enable_aws ? 1 : 0

  name        = "vanguard-dummy-vm"
  description = "Dummy VM firewall: outbound traffic only"
  vpc_id      = aws_vpc.dummy[0].id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "vanguard-dummy-vm-firewall" }
}

resource "aws_instance" "dummy" {
  count = var.enable_aws ? 1 : 0

  ami                         = data.aws_ami.amazon_linux[0].id
  instance_type               = "t3.micro"
  subnet_id                   = aws_subnet.dummy[0].id
  vpc_security_group_ids      = [aws_security_group.vm[0].id]
  associate_public_ip_address = false

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 8
    delete_on_termination = true
    encrypted             = true
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  tags = { Name = "vanguard-dummy-vm" }
}

resource "aws_dynamodb_table" "dummy" {
  count = var.enable_aws ? 1 : 0

  name         = "vanguard-dummy-database"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }

  deletion_protection_enabled = false
  tags                        = { Name = "vanguard-dummy-database" }
}

output "aws_dummy_inventory" {
  description = "Identifiers created for the AWS inventory test."
  value = var.enable_aws ? {
    vpc_id        = aws_vpc.dummy[0].id
    instance_id   = aws_instance.dummy[0].id
    database_id   = aws_dynamodb_table.dummy[0].arn
    database_name = aws_dynamodb_table.dummy[0].name
  } : null
}
