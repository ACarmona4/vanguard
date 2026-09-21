# AWS Academy: reuse the supplied instance profile; never create or edit IAM roles.
variable "aws_instance_profile_name" {
  description = "Existing profile supplied by AWS Academy, used by the telemetry agent and SSM."
  type        = string
  default     = "LabInstanceProfile"
}

data "aws_caller_identity" "current" {
  count = var.enable_aws ? 1 : 0
}

data "aws_iam_instance_profile" "telemetry" {
  count = var.enable_aws ? 1 : 0
  name  = var.aws_instance_profile_name
}

locals {
  agent_bucket  = var.enable_aws ? "vanguard-agent-${data.aws_caller_identity.current[0].account_id}-${var.aws_region}" : ""
  agent_release = jsondecode(file("${path.module}/../observability/aws-agent-release.json"))
  agent_package = "otelcol-contrib_${local.agent_release.version}_linux_amd64.tar.gz"
  agent_config  = file("${path.module}/../observability/aws-host-agent.yaml")
  agent_install = var.enable_aws ? templatefile("${path.module}/scripts/install-aws-agent.sh.tftpl", {
    region         = var.aws_region
    bucket         = local.agent_bucket
    package_key    = aws_s3_object.agent[0].key
    package_sha256 = local.agent_release.sha256
    config_base64  = base64encode(local.agent_config)
    generation     = sha256("${local.agent_release.sha256}:${local.agent_config}:${file("${path.module}/scripts/install-aws-agent.sh.tftpl")}")
  }) : ""
}

# Bucket bootstrap uses only Academy-supported S3 APIs (see aws_lab.py).
# Retain the existing package bucket when migrating from the full S3 resource.
removed {
  from = aws_s3_bucket.agent
  lifecycle {
    destroy = false
  }
}

resource "aws_s3_bucket_public_access_block" "agent" {
  count                   = var.enable_aws ? 1 : 0
  bucket                  = local.agent_bucket
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "agent" {
  count  = var.enable_aws ? 1 : 0
  bucket = local.agent_bucket
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_object" "agent" {
  count       = var.enable_aws ? 1 : 0
  bucket      = local.agent_bucket
  key         = "otel/${local.agent_release.sha256}/${local.agent_package}"
  source      = "${path.module}/../.vanguard/agent/${local.agent_package}"
  source_hash = local.agent_release.sha256
  depends_on  = [aws_s3_bucket_public_access_block.agent, aws_s3_bucket_server_side_encryption_configuration.agent]
}

resource "aws_cloudwatch_log_group" "host_metrics" {
  count             = var.enable_aws ? 1 : 0
  name              = "/vanguard/host-metrics"
  retention_in_days = 3
}

resource "aws_security_group" "telemetry_endpoints" {
  count       = var.enable_aws ? 1 : 0
  name        = "vanguard-telemetry-endpoints"
  description = "HTTPS from the private dummy VM to AWS telemetry and management endpoints"
  vpc_id      = aws_vpc.dummy[0].id
  ingress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.vm[0].id]
  }
}

resource "aws_vpc_endpoint" "telemetry" {
  for_each            = var.enable_aws ? toset(["ssm", "ssmmessages", "logs"]) : toset([])
  vpc_id              = aws_vpc.dummy[0].id
  service_name        = "com.amazonaws.${var.aws_region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = [aws_subnet.dummy[0].id]
  security_group_ids  = [aws_security_group.telemetry_endpoints[0].id]
}

resource "aws_vpc_endpoint" "agent_s3" {
  count             = var.enable_aws ? 1 : 0
  vpc_id            = aws_vpc.dummy[0].id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_vpc.dummy[0].main_route_table_id]
}

# The tag scopes installation to infrastructure explicitly managed by this lab.
resource "aws_ssm_association" "telemetry" {
  count            = var.enable_aws ? 1 : 0
  name             = "AWS-RunShellScript"
  association_name = "vanguard-host-metrics"
  targets {
    key    = "tag:VanguardAgent"
    values = ["managed"]
  }
  parameters = {
    commands         = "bash <<'VANGUARD_INSTALL'\n${local.agent_install}\nVANGUARD_INSTALL"
    executionTimeout = "600"
  }
  schedule_expression = "rate(30 minutes)"
  max_concurrency     = "1"
  max_errors          = "1"
  depends_on          = [aws_instance.dummy, aws_vpc_endpoint.telemetry, aws_vpc_endpoint.agent_s3, aws_cloudwatch_log_group.host_metrics]
}
