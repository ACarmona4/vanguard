variable "enable_gcp" {
  description = "Create the disposable GCP inventory lab. Disabled by default."
  type        = bool
  default     = false
}

variable "gcp_project_id" {
  description = "GCP project used by the disposable lab."
  type        = string
  default     = "project-174e1e0a-685b-4103-a18"
}

variable "gcp_region" {
  description = "GCP region used by the disposable lab."
  type        = string
  default     = "us-central1"
}

variable "gcp_zone" {
  description = "GCP zone used by the disposable VM."
  type        = string
  default     = "us-central1-a"
}

provider "google" {
  project      = var.gcp_project_id
  region       = var.gcp_region
  zone         = var.gcp_zone
  access_token = var.enable_gcp ? null : "disabled"
}

resource "google_project_service" "compute" {
  count = var.enable_gcp ? 1 : 0

  project            = var.gcp_project_id
  service            = "compute.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "sqladmin" {
  count = var.enable_gcp ? 1 : 0

  project            = var.gcp_project_id
  service            = "sqladmin.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "service_networking" {
  count = var.enable_gcp ? 1 : 0

  project            = var.gcp_project_id
  service            = "servicenetworking.googleapis.com"
  disable_on_destroy = false
}

data "google_compute_image" "debian" {
  count = var.enable_gcp ? 1 : 0

  family  = "debian-12"
  project = "debian-cloud"
}

resource "random_id" "gcp_database_suffix" {
  count = var.enable_gcp ? 1 : 0

  byte_length = 3
}

resource "google_compute_network" "dummy" {
  count = var.enable_gcp ? 1 : 0

  name                    = "vanguard-dummy-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"

  depends_on = [google_project_service.compute]
}

resource "google_compute_subnetwork" "dummy" {
  count = var.enable_gcp ? 1 : 0

  name                     = "vanguard-dummy-subnet"
  region                   = var.gcp_region
  network                  = google_compute_network.dummy[0].id
  ip_cidr_range            = "10.30.0.0/24"
  private_ip_google_access = true
}

resource "google_compute_firewall" "iap_ssh" {
  count = var.enable_gcp ? 1 : 0

  name          = "vanguard-dummy-allow-iap-ssh"
  network       = google_compute_network.dummy[0].name
  direction     = "INGRESS"
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["vanguard-dummy-vm"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_instance" "dummy" {
  count = var.enable_gcp ? 1 : 0

  name                      = "vanguard-dummy-vm"
  machine_type              = "e2-micro"
  zone                      = var.gcp_zone
  allow_stopping_for_update = true
  deletion_protection       = false
  tags                      = ["vanguard-dummy-vm"]

  labels = {
    project     = "vanguard-inventory"
    environment = "dummy"
    managed-by  = "terraform"
    disposable  = "true"
  }

  boot_disk {
    auto_delete = true

    initialize_params {
      image = data.google_compute_image.debian[0].self_link
      size  = 10
      type  = "pd-standard"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.dummy[0].id
  }

  metadata = {
    enable-oslogin = "TRUE"
  }
}

resource "google_compute_global_address" "private_services" {
  count = var.enable_gcp ? 1 : 0

  name          = "vanguard-dummy-private-services"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.dummy[0].id

  depends_on = [google_project_service.service_networking]
}

resource "google_service_networking_connection" "private_services" {
  count = var.enable_gcp ? 1 : 0

  network                 = google_compute_network.dummy[0].id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services[0].name]

  depends_on = [google_project_service.service_networking]
}

resource "google_sql_database_instance" "dummy" {
  count = var.enable_gcp ? 1 : 0

  name                = "vanguard-dummy-${random_id.gcp_database_suffix[0].hex}"
  region              = var.gcp_region
  database_version    = "POSTGRES_15"
  deletion_protection = false

  settings {
    tier              = "db-f1-micro"
    edition           = "ENTERPRISE"
    availability_type = "ZONAL"
    disk_type         = "PD_HDD"
    disk_size         = 10
    disk_autoresize   = false

    backup_configuration {
      enabled                        = false
      point_in_time_recovery_enabled = false
    }

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.dummy[0].self_link
    }

    deletion_protection_enabled = false

    user_labels = {
      project     = "vanguard-inventory"
      environment = "dummy"
      managed-by  = "terraform"
      disposable  = "true"
    }
  }

  depends_on = [
    google_project_service.sqladmin,
    google_service_networking_connection.private_services,
  ]
}

resource "google_sql_database" "dummy" {
  count = var.enable_gcp ? 1 : 0

  name     = "vanguard"
  instance = google_sql_database_instance.dummy[0].name
}

output "gcp_dummy_inventory" {
  description = "Identifiers created for the GCP inventory test."
  value = var.enable_gcp ? {
    vpc_id              = google_compute_network.dummy[0].id
    instance_id         = google_compute_instance.dummy[0].instance_id
    database_name       = google_sql_database_instance.dummy[0].name
    database_private_ip = google_sql_database_instance.dummy[0].private_ip_address
  } : null
}
