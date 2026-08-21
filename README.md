<div align="center">

# 🏠 Gismo's Home Operations (`home-ops`)

_My personal Kubernetes homelab cluster, fully managed with GitOps, Talos Linux, and Flux CD._

<p align="center">
  <a href="https://github.com/gismo2004/home-ops/actions/workflows/flate.yaml"><img src="https://img.shields.io/github/actions/workflow/status/gismo2004/home-ops/flate.yaml?label=Flate%20Diff&style=for-the-badge&logo=github" alt="Flate Status"></a>
  <a href="https://github.com/fluxcd/flux2"><img src="https://img.shields.io/badge/GitOps-Flux%20v2-blue?style=for-the-badge&logo=flux" alt="Flux"></a>
  <a href="https://www.talos.dev/"><img src="https://img.shields.io/badge/OS-Talos%20Linux-orange?style=for-the-badge&logo=linux" alt="Talos"></a>
  <a href="https://cilium.io/"><img src="https://img.shields.io/badge/CNI-Cilium%20eBPF-brightgreen?style=for-the-badge&logo=cilium" alt="Cilium"></a>
  <a href="https://renovatebot.com"><img src="https://img.shields.io/badge/Dependencies-Renovate-violet?style=for-the-badge&logo=renovatebot" alt="Renovate"></a>
</p>

</div>

---

## 📖 Overview

This repository is the single source of truth for my bare-metal/virtualized homelab infrastructure. Everything is declared in YAML and reconciled continuously via **Flux v2** following GitOps best practices.

### ⚙️ Core Architecture & Technologies

| Layer                      | Component                                                   | Description                                                           |
| :------------------------- | :---------------------------------------------------------- | :-------------------------------------------------------------------- |
| **Hypervisor**             | [Proxmox VE](https://www.proxmox.com/) (`10.0.0.3`)         | Bare-metal host virtualizing compute & storage                        |
| **Operating System**       | [Talos Linux](https://www.talos.dev/)                       | Immutable, secure, API-driven Linux OS                                |
| **GitOps Controller**      | [Flux CD](https://fluxcd.io/)                               | Automated reconciliation from GitHub                                  |
| **Networking & CNI**       | [Cilium](https://cilium.io/)                                | eBPF-powered CNI with KubeProxyReplacement & L2 LoadBalancer IPAM     |
| **Ingress & Gateway**      | [Envoy Gateway](https://gateway.envoyproxy.io/)             | Kubernetes Gateway API implementation for internal & external routing |
| **Certificate Management** | [Cert-Manager](https://cert-manager.io/)                    | Automatic Let's Encrypt TLS certificates via Cloudflare DNS-01        |
| **Secrets Management**     | [SOPS](https://github.com/getsops/sops) + Age               | Git-encrypted secrets decrypted at apply-time by Flux                 |
| **Storage Engine**         | [Miroir](https://github.com/home-operations/miroir) + ZFS   | Local node-backed PV storage and VolumeSnapshots                      |
| **Backup & DR**            | [Kopiur](https://github.com/home-operations/kopiur) + Kopia | Automated snapshot policies, compression, and deduplicated backups    |
| **Database**               | [CloudNativePG](https://cloudnative-pg.io/)                 | High-availability PostgreSQL operator with automated WAL archiving    |
| **Observability**          | Prometheus, Grafana, VictoriaLogs                           | Full stack metrics, logs, hardware sensors, and hypervisor telemetry  |

---

## 📦 Workloads & Applications

All workloads run in dedicated namespaces and are accessed securely via Gateway API routes (`*.gismo2004.cc`).

### 🏡 Home Automation & IoT

- ⚡ **[Home Assistant](https://home-assistant.io/)**: Home automation platform backed by CloudNative PostgreSQL. Includes integrated `code-server` sidecar.
- 📶 **[ESPHome](https://esphome.io/)**: Dashboard and compiler for custom ESP8266/ESP32 sensor firmware.
- 📨 **[Mosquitto](https://mosquitto.org/)**: Dedicated MQTT message broker for IoT devices.
- 🔌 **[TasmoAdmin](https://tasmoadmin.com/)**, **TasmoBackup**, **TasmoCompiler**: Suite for managing, backing up, and compiling Tasmota devices.
- 📺 **[OSCam](https://www.oscam.cc/)**: Open Source Conditional Access Module with web management and `code-server` editor.

### 🎬 Media & Downloads

- 🍿 **[Jellyfin](https://jellyfin.org/)**: Self-hosted media streaming server with NVIDIA NVENC/NVDEC hardware acceleration.
- 📚 **[Kavita](https://www.kavitareader.com/)**: Responsive manga, comics, and eBook reader.
- 📖 **[Calibre](https://calibre-ebook.com/)**: Complete eBook management library and content server.
- 🖼️ **[Photoview](https://photoview.217.at/)**: Fast, photo gallery backed by CloudNative-PG PostgreSQL.
- 📥 **[JDownloader 2](https://jdownloader.org/)**: Automated download manager with web interface.
- ⚡ **The `*arr` Suite**:
    - **[Radarr](https://radarr.video/)**: Movie management & automation
    - **[Sonarr](https://sonarr.tv/)**: TV show management & automation
    - **[Lidarr](https://lidarr.audio/)**: Music collection manager
    - **[Prowlarr](https://prowlarr.com/)**: Indexer manager and proxy
    - **[SABnzbd](https://sabnzbd.org/)**: Usenet downloader
    - **[Unmonitarr](https://github.com/unmonitarr/unmonitarr)**: Automated unmonitored media cleaner

### 🛠️ Productivity & Utilities

- 🍳 **[Mealie](https://mealie.io/)**: Recipe manager and meal planner with Gemini AI integration and PostgreSQL backend.
- 🧹 **[Krokiet](https://github.com/qarmin/czkawka)**: High-performance duplicate file and empty folder finder.
- 🖨️ **[OrcaSlicer](https://github.com/SoftFever/OrcaSlicer)**: 3D printing slicer accessible via browser.
- 📻 **[EdgeTX](https://edgetx.org/)**: RC radio firmware companion and storage manager.
- 🧊 **[IceAgent](https://github.com/gismo2004/ice_agent)**: Custom automation service with integrated `code-server`.
- 📡 **[VDF](https://github.com/gismo2004/home-ops)**: Vodafone cable connection monitor and automated reconnection tool.

### 📊 Observability (`o11y`)

- 📈 **[Grafana](https://grafana.com/)**: Visualizations and dashboards managed by `grafana-operator`.
- ⏱️ **[Prometheus](https://prometheus.io/)**: Metric collection, alerting rules, and storage.
- 📜 **[VictoriaLogs](https://victoriametrics.com/products/victorialogs/)**: High-performance log aggregation engine.
- 🛡️ **[Blocky](https://0xerr0r.github.io/blocky/)**: Fast, privacy-focused DNS proxy and ad-blocker with interactive Grafana controls.
- 🖥️ **[PVE Exporter](https://github.com/prometheus-pve/prometheus-pve-exporter)**: Proxmox VE hypervisor and VM/LXC telemetry.
- 🔍 **[Blackbox Exporter](https://github.com/prometheus/blackbox_exporter)**: Network probe and HTTP/DNS uptime monitor.
- 💾 **[Kopiur](https://github.com/home-operations/kopiur)**: VolumeSnapshot lifecycle and backup operator.

---

## 🗂️ Directory Structure

```text
.
├── .github/                 # GitHub Actions workflows and Renovate config
│   ├── renovate.json5       # Dependency update rules
│   └── workflows/           # CI automation (Flate diffs, labels)
├── kubernetes/
│   ├── apps/                # Application manifests grouped by namespace
│   │   ├── default/         # User applications (Jellyfin, Home Assistant, Mealie...)
│   │   ├── network/         # Ingress, Gateway API, Cloudflare DNS
│   │   ├── o11y/            # Observability (Prometheus, Grafana, VictoriaLogs)
│   │   ├── kopiur-system/   # Backup operator configuration
│   │   └── kube-system/     # Core CNI, CoreDNS, Snapshot Controller
│   ├── components/          # Reusable Kustomize components (SOPS, Kopiur, Gatus)
│   └── flux/                # Flux cluster bootstrap and root Kustomizations
└── talos/                   # Talos Linux machine configs and patches
```

---

## 🔄 How the Automated Status Updates Work

If you've seen automated status badges, live uptime reports, or service inventories in cluster repositories (like onedr0p or bjw-s), here is how they operate:

### 1. Uptime & Service Health (Gatus / Uptime Kuma)

- **How it works**: A lightweight probe engine like **[Gatus](https://gatus.io/)** periodically tests all endpoints (`*.gismo2004.cc/healthz`).
- **README Badge**: Gatus generates an SVG badge or JSON endpoint that connects to [Shields.io](https://shields.io) (e.g. `https://img.shields.io/endpoint?url=https://status.gismo2004.cc/api/v1/endpoints/...`).

### 2. CI/CD Diff Automation (Flate)

- **How it works**: The **[flate](https://github.com/home-operations/flate)** GitHub Action runs on every Pull Request to render exact diffs of HelmReleases and Kustomizations before they are merged.

### 3. Automated Inventory / Cluster Status

- **How it works**: A scheduled GitHub Actions workflow runs `kubectl get helmreleases -A` (or inspects Git manifests) and commits an updated Markdown table with current app versions and container tags directly into the `README.md`.

---

<div align="center">
  <i>Maintained with ❤️ by <a href="https://github.com/gismo2004">@gismo2004</a></i>
</div>
