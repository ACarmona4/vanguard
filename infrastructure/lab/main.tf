terraform {
  required_version = ">= 1.7.0"
  required_providers {
    aws    = { source = "hashicorp/aws", version = ">= 5.0, < 7.0" }
    google = { source = "hashicorp/google", version = ">= 6.0, < 8.0" }
  }
}

variable "provider_name" { type = string }
variable "deployment_id" { type = string }
variable "region" { type = string }
variable "gcp_project_id" { type = string }

locals {
  aws  = var.provider_name == "aws"
  gcp  = var.provider_name == "gcp"
  name = "vanguard-${var.deployment_id}"
}

provider "aws" { region = local.aws ? var.region : "us-east-1" }
provider "google" {
  project = var.gcp_project_id
  region  = local.gcp ? var.region : "us-central1"
}

data "aws_availability_zones" "available" {
  count = local.aws ? 1 : 0
  state = "available"
}

data "aws_ami" "linux" {
  count       = local.aws ? 1 : 0
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
}

resource "aws_vpc" "lab" {
  count                = local.aws ? 1 : 0
  cidr_block           = "10.42.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${local.name}-vpc", ManagedBy = "vanguard", Disposable = "true" }
}

resource "aws_subnet" "lab" {
  count             = local.aws ? 1 : 0
  vpc_id            = aws_vpc.lab[0].id
  cidr_block        = "10.42.1.0/24"
  availability_zone = data.aws_availability_zones.available[0].names[0]
  tags              = { Name = "${local.name}-subnet", ManagedBy = "vanguard" }
}

resource "aws_security_group" "lab" {
  count       = local.aws ? 1 : 0
  name_prefix = "${local.name}-"
  description = "Vanguard disposable lab: outbound only"
  vpc_id      = aws_vpc.lab[0].id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${local.name}-firewall", ManagedBy = "vanguard" }
}

resource "aws_instance" "lab" {
  count                       = local.aws ? 1 : 0
  ami                         = data.aws_ami.linux[0].id
  instance_type               = "t3.micro"
  subnet_id                   = aws_subnet.lab[0].id
  vpc_security_group_ids      = [aws_security_group.lab[0].id]
  associate_public_ip_address = false
  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }
  root_block_device {
    encrypted   = true
    volume_size = 8
    volume_type = "gp3"
  }
  tags = { Name = "${local.name}-vm", ManagedBy = "vanguard", Disposable = "true" }
}

resource "aws_dynamodb_table" "lab" {
  count        = local.aws ? 1 : 0
  name         = "${local.name}-database"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"
  attribute {
    name = "id"
    type = "S"
  }
  tags = { Name = "${local.name}-database", ManagedBy = "vanguard", Disposable = "true" }
}

resource "google_project_service" "compute" {
  count              = local.gcp ? 1 : 0
  project            = var.gcp_project_id
  service            = "compute.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "sql" {
  count              = local.gcp ? 1 : 0
  project            = var.gcp_project_id
  service            = "sqladmin.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "service_networking" {
  count              = local.gcp ? 1 : 0
  project            = var.gcp_project_id
  service            = "servicenetworking.googleapis.com"
  disable_on_destroy = false
}

resource "google_compute_network" "lab" {
  count                   = local.gcp ? 1 : 0
  name                    = "${local.name}-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.compute]
}

resource "google_compute_subnetwork" "lab" {
  count         = local.gcp ? 1 : 0
  name          = "${local.name}-subnet"
  region        = var.region
  network       = google_compute_network.lab[0].id
  ip_cidr_range = "10.43.1.0/24"
}

resource "google_compute_instance" "lab" {
  count        = local.gcp ? 1 : 0
  name         = "${local.name}-vm"
  machine_type = "e2-micro"
  zone         = "${var.region}-a"
  boot_disk {
    auto_delete = true
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 10
      type  = "pd-standard"
    }
  }
  network_interface {
    subnetwork = google_compute_subnetwork.lab[0].id
  }
  metadata = { enable-oslogin = "TRUE" }
  labels   = { managed-by = "vanguard", disposable = "true" }
}

resource "google_compute_global_address" "private_services" {
  count         = local.gcp ? 1 : 0
  name          = "${local.name}-private-services"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.lab[0].id
  depends_on    = [google_project_service.service_networking]
}

resource "google_service_networking_connection" "private_services" {
  count                   = local.gcp ? 1 : 0
  network                 = google_compute_network.lab[0].id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services[0].name]
  depends_on              = [google_project_service.service_networking]
}

resource "google_sql_database_instance" "lab" {
  count               = local.gcp ? 1 : 0
  name                = "${local.name}-db"
  region              = var.region
  database_version    = "POSTGRES_15"
  deletion_protection = false
  settings {
    tier              = "db-f1-micro"
    edition           = "ENTERPRISE"
    availability_type = "ZONAL"
    disk_type         = "PD_HDD"
    disk_size         = 10
    disk_autoresize   = false
    backup_configuration { enabled = false }
    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.lab[0].self_link
    }
    deletion_protection_enabled = false
    user_labels                 = { managed-by = "vanguard", disposable = "true" }
  }
  depends_on = [google_project_service.sql, google_service_networking_connection.private_services]
}

resource "google_sql_database" "lab" {
  count    = local.gcp ? 1 : 0
  name     = "vanguard"
  instance = google_sql_database_instance.lab[0].name
}

output "resources" {
  value = local.aws ? {
    vpc      = aws_vpc.lab[0].id
    vm       = aws_instance.lab[0].id
    database = aws_dynamodb_table.lab[0].name
    } : {
    vpc      = google_compute_network.lab[0].id
    vm       = tostring(google_compute_instance.lab[0].instance_id)
    database = google_sql_database_instance.lab[0].name
  }
}
