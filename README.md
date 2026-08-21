<div align="center">

# 🏠 Gismo's Home Operations (`home-ops`)

_A Kubernetes homelab cluster, fully managed with GitOps, Talos Linux, and Flux CD._

<p align="center">
  <a href="https://github.com/gismo2004/home-ops/actions/workflows/flate.yaml"><img src="https://img.shields.io/github/actions/workflow/status/gismo2004/home-ops/flate.yaml?label=Flate%20Diff&style=for-the-badge&logo=github" alt="Flate Status"></a>
  <a href="https://status.gismo2004.cc"><img src="https://img.shields.io/badge/Status-Gatus-darkgreen?style=for-the-badge&logo=statuspage" alt="Status Page"></a>
  <a href="https://github.com/fluxcd/flux2"><img src="https://img.shields.io/badge/GitOps-Flux%20v2-blue?style=for-the-badge&logo=flux" alt="Flux"></a>
  <a href="https://www.talos.dev/"><img src="https://img.shields.io/badge/OS-Talos%20Linux-orange?style=for-the-badge&logo=linux" alt="Talos"></a>
  <a href="https://cilium.io/"><img src="https://img.shields.io/badge/CNI-Cilium%20eBPF-brightgreen?style=for-the-badge&logo=cilium" alt="Cilium"></a>
  <a href="https://renovatebot.com"><img src="https://img.shields.io/badge/Dependencies-Renovate-violet?style=for-the-badge&logo=renovatebot" alt="Renovate"></a>
</p>

</div>

---

## 📖 Overview

This repository contains the complete declarative infrastructure and application definitions for my homelab cluster. Everything is managed via **GitOps** and continuously reconciled by **Flux v2**.

### ⚙️ Architecture & Core Components

| Component               | Technology                                                  | Description                                                               |
| :---------------------- | :---------------------------------------------------------- | :------------------------------------------------------------------------ |
| **Operating System**    | [Talos Linux](https://www.talos.dev/)                       | Immutable, secure, minimal Linux distribution for Kubernetes              |
| **GitOps Controller**   | [Flux CD](https://fluxcd.io/)                               | Automated synchronization and lifecycle management from Git               |
| **Networking & CNI**    | [Cilium](https://cilium.io/)                                | High-performance eBPF CNI with native L2 LoadBalancer IPAM                |
| **Ingress & Gateway**   | [Envoy Gateway](https://gateway.envoyproxy.io/)             | Kubernetes Gateway API implementation for internal routing                |
| **Certificates**        | [Cert-Manager](https://cert-manager.io/)                    | Automated TLS certificate provisioning via Cloudflare DNS-01              |
| **Secrets Management**  | [SOPS](https://github.com/getsops/sops) + Age               | Git-encrypted secrets with automatic decryption by Flux                   |
| **Storage Driver**      | [Miroir CSI](https://github.com/home-operations/miroir)     | Lightweight local node storage CSI with VolumeSnapshot support            |
| **Backups & Snapshots** | [Kopiur](https://github.com/home-operations/kopiur) + Kopia | Automated snapshot schedules, deduplication, and repository management    |
| **Database Engine**     | [CloudNativePG](https://cloudnative-pg.io/)                 | High-availability PostgreSQL operator with automated WAL archiving        |
| **Observability**       | Prometheus, Grafana, VictoriaLogs                           | Comprehensive metrics, log aggregation, alerting, and hardware monitoring |

---

## 🗂️ Workload Categories

Workloads are deployed in isolated namespaces and structured logically:

- **🏠 Home Automation & IoT**: Centralized automation hubs, MQTT messaging, and ESP/microcontroller management tools.
- **🍿 Media & Entertainment**: Hardware-accelerated media streaming, reading platforms, and automated content collectors.
- **🛠️ Utilities & Productivity**: Recipe management, print slicing, duplicate finders, and system maintenance utilities.
- **📊 Observability (`o11y`)**: Full-stack telemetry including node sensors, hypervisor statistics, container logs, and DNS blocking metrics.

---

## 📁 Repository Structure

```text
.
├── .github/                 # GitHub Actions workflows and Renovate configuration
├── kubernetes/
│   ├── apps/                # Application manifests grouped by namespace
│   │   ├── default/         # User applications and services
│   │   ├── network/         # Ingress, Gateway API, and DNS routing
│   │   ├── o11y/            # Observability (Prometheus, Grafana, VictoriaLogs)
│   │   ├── kopiur-system/   # Backup and snapshot policies
│   │   └── kube-system/     # Core CNI, CoreDNS, and snapshot controller
│   ├── components/          # Reusable Kustomize components (SOPS, Kopiur, Gatus)
│   └── flux/                # Flux cluster bootstrap and root Kustomizations
└── talos/                   # Talos Linux machine configurations and patches
```

---

<div align="center">
  <i>Maintained with ❤️ by <a href="https://github.com/gismo2004">@gismo2004</a></i>
</div>
