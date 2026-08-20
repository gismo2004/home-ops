# Migration from HomeCluster to home-ops

Bootstrap this repo from cluster-template first, get an empty cluster healthy,
then bring apps over one at a time from `~/git_repos/HomeCluster`.

## State of the old cluster (as of 2026-08-20 11:xx)

Quiesced and waiting, **not** destroyed:

- Kopiur: **28/28 snapshots Succeeded**, tag `migration-202608201101`
- CNPG: all three DBs backed up _after_ the apps stopped, so quiesced —
  hass 11:19:55, mealie 11:09:13, photoview 11:09:11, zero failures
- 26 app Deployments at 0 replicas, their HelmReleases `flux suspend`ed
- blocky deliberately still running — it is the LAN's DNS and adblocker
- Reverse any of it with `flux resume helmrelease <name> -n <ns>`

## Decide these BEFORE the Talos install — they cannot be changed after

1. **Install disk.** `talos/topf.yaml` says `/dev/sda`, which is the disk the
   current cluster runs on. The entire reason for rebuilding is that its BOOT
   partition is 1.049 GB and fixed at install time. Installing to `/dev/sda`
   destroys the old cluster _and_ leaves no separate disk for storage. The plan
   of record was: install Talos to a **new/second** virtual disk so the 1.1 TB
   disk becomes the storage pool.
2. **Storage layer.** miroir needs an LVM-thin pool on a dedicated partition
   **declared at install time**. Deciding this afterwards means reinstalling.
3. **Node IP is 10.0.0.48 — the same as the live node.** Bootstrapping takes
   over the running cluster. That is the point of no return.

## Before the point of no return

**Prove a restore, do not assume one.** While the old cluster still exists,
restore one app's PVC _and_ one database into throwaway objects and check the
contents. mealie is the cheapest test — it has both. A snapshot reporting
`Succeeded` is not evidence that a restore works; that exact assumption
produced an empty PVC once already on this project.

Also worth clearing first: the CNPG operator has ~20 restarts with
`leader election lost` (API-server timeouts under disk I/O). It completes
backups, but it is also what performs **restores**.

## Bootstrap sequence

1. Rotate the Cloudflare API token — it sits in plaintext in `cluster.toml`.
   (`cluster.toml`, `age.key`, `deploy.key*` are gitignored; the repo is
   PUBLIC. `talos/secrets.sops.yaml` is committed but sops-encrypted.)
2. First commit + push (repo currently has **0 commits**).
3. Talos install → `just talos apply` → bootstrap etcd.
4. `just bootstrap` — cilium, coredns, spegel, flux. Cluster Ready, no apps.
5. Restore infrastructure **before** any app: Kopiur `ClusterRepository`
   pointed at the _same_ S3 bucket, the CNPG operator, a
   `VolumeSnapshotClass`, and the `cluster-secrets` sops Secret.

## App-by-app migration

Order: krokiet (trivial, proves the pattern) → arr-suite → jellyfin →
mealie/photoview (PVC + database) → home-assistant (most valuable) →
**blocky last**, because it is the LAN's DNS.

Per app, the two things that silently lose data:

- **Snapshot identity is `<policy>@<namespace>:/pvc/<claim>`.** Using the
  shared `components/kopiur/backup`, `KOPIUR_NAME` must equal the OLD policy
  name and `KOPIUR_CLAIM` the OLD claim name. They frequently differ (policy
  `calibre` backs up claim `calibre-config`). Get it wrong and the mover
  computes a different identity, finds nothing, and populates an **empty**
  volume.
- **Set `Restore.spec.policy.onMissingSnapshot: Fail`** during migration.
  The usual `Continue` turns "no snapshot matched" into a silently empty PVC.

For CNPG apps: recover via `externalClusters` pointing at the OLD serverName,
and give `spec.backup` a **different** serverName, or the restore job's
`barman-cloud-check-wal-archive` refuses with `Expected empty archive`.

Keep the old cluster's disk and the S3 bucket untouched until every app is
verified here.
