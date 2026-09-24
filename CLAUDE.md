# CLAUDE.md

Guidance for Claude Code (or any agent) working in this repository. Treat this as authoritative
for how this specific cluster behaves: it records load-bearing facts and known landmines that
aren't obvious from reading the manifests alone.

## What this is

A FluxCD GitOps repo for a single-node Talos Linux Kubernetes homelab cluster running as a Proxmox
VM. Every cluster change is a git commit to `origin/main`; Flux reconciles the live cluster to
match the repo, not the other way around. Modelled on
[onedr0p/home-ops](https://github.com/onedr0p/home-ops) and bootstrapped from
[siderolabs/cluster-template](https://github.com/siderolabs/cluster-template). Apps and data were
migrated from a prior cluster (`gismo2004/HomeCluster`, historical only, see `docs/MIGRATION.md`).

There is no traditional test suite. "Correct" means valid Kubernetes/Kustomize/Helm YAML that
Flux can reconcile, see "Validating a change" below.

## Environment & tooling

Tool versions are pinned in `.mise/config.toml` (the top-level `mise.toml` is an empty leftover
of the template). Run `mise install` once per checkout; its `[env]` block exports `KUBECONFIG`,
`SOPS_AGE_KEY_FILE`, `SOPS_CONFIG` and `TALOSCONFIG` from repo-relative paths. `kubectl` and
`talosctl` pins track the live cluster.

Common commands (`just --list --list-submodules` for the full menu):

- `just kube reconcile`: force Flux to pull. A GitHub webhook does this on every push, so only
  use it when reconciliation genuinely seems stuck.
- `just talos diff` / `just talos apply` / `apply-node <node>`: preview and apply machine config
  (shows a diff and asks first). `just talos upgrade-node <node>`, `just talos upgrade-k8s`.
- `just bootstrap talos` / `just bootstrap apps`: the two-phase cluster bootstrap.
- `sops -d <file>`: decrypt a `*.sops.yaml`. Never print or commit decrypted content. Encryption
  is a manual `sops -e -i <file>`; no hook does it.
- Pre-commit (`lefthook`) formats staged YAML/JSON/Markdown with `oxfmt`, formats `.justfile`s and
  mise config, and runs `zizmor` on workflows. If it fails with `oxfmt: not found`, run
  `export PATH="$HOME/.local/share/mise/shims:$PATH"` rather than skipping the hook.

## Repository layout

```
kubernetes/
  flux/cluster/ks.yaml     the root Kustomization ("cluster-apps"), applies ./kubernetes/apps
  apps/<group>/<app>/
    ks.yaml                the Flux Kustomization, points at ./app, sets targetNamespace
    app/kustomization.yaml lists that app's resource files
    app/helmrelease.yaml   almost always app-template, via chartRef -> app/ocirepository.yaml
  components/
    sops/                  cluster-secrets component (its one key is unused, see below)
    kopiur/backup/         shared backup wiring, see "Backups: Kopiur"
bootstrap/                 helmfile-driven CRD/core-component bootstrap
talos/                     topf-rendered machine config (topf.yaml + fragments)
docs/MIGRATION.md          the HomeCluster -> home-ops rebuild runbook; its per-app landmines
                           (backup identity, dual serverName) are still live facts
```

**`apps/default/` is a directory grouping, not the `default` namespace.** Only `echo` deploys
there; every other app gets `targetNamespace: <app-name>` in its `ks.yaml`. Exception: `jellyfin`,
`lidarr`, `prowlarr`, `radarr`, `sabnzbd` and `sonarr` share `media`, inherited from the old stack.
Splitting them is **not** a rename, because the namespace is part of the backup identity (see
"Backups: Kopiur") and needs the identity-preserving procedure from `docs/MIGRATION.md`.

**Hostnames and IPs are literal values, not `${VAR}` substitutions.** The `cluster-secrets` Secret
behind `components/sops` still defines `SECRET_DOMAIN`, but nothing references it. Copy the
convention from a recent sibling app, not from older examples. Genuinely sensitive values get a
per-app `app/secret.sops.yaml`.

**Never `kubectl apply -f` a repo file containing unresolved `${...}` placeholders:** it writes the
literal placeholder into the live object.

## Workflow: git+Flux for configuration, kubectl for state changes

Configuration (a resource's desired spec) belongs in git. State changes (scaling a Deployment,
deleting a pod or a stray cache PVC, triggering an existing CR) are fine via `kubectl`. A live
`kubectl patch` on something that _is_ configuration is silently reverted on the next reconcile.

**Per-app patches go in `app/kustomization.yaml`, never in `ks.yaml`'s `spec.patches`.** The root
`cluster-apps` Kustomization patches a `spec.patches` block onto every child, and that merge
replaces the list wholesale, so a child's own entries are dropped without any error. A `patches:`
block in `app/kustomization.yaml` works, also on component-generated resources, and shows up in a
local `kustomize build`. Target by `group`+`kind` only: `postBuild.substitute` runs after the
build, so at patch time a component object is still literally named `${KOPIUR_NAME}`.

## Validating a change before merge

- `kustomize build kubernetes/apps/<group>/<app>/app`, optionally piped into
  `kubeconform -strict -ignore-missing-schemas`. Literal `${KOPIUR_*}` placeholders in the output
  are expected; Flux substitutes them.
- CI's `flate` workflow runs `flate test all` against `kubernetes/flux/cluster` on every PR touching
  `kubernetes/**`. It is the closest thing to a dry-run of Flux's dependency graph; a clean
  `kustomize build` does not guarantee it passes. It never starts a container.
- For anything touching Kopiur/CNPG backup identity, don't trust a `Succeeded` status alone:
  restore into a throwaway object and check real content.

## Networking: Multus for LAN-facing pods

Pods live on Cilium's pod network (`172.16.0.0/24`) and never see link-local multicast from the
home LAN (`10.0.0.0/24`), so mDNS discovery does not reach them. Multus (`kube-system/multus`)
gives selected pods a second, macvlan interface on `bond0` via the `lan`
`NetworkAttachmentDefinition` and a `k8s.v1.cni.cncf.io/networks` pod annotation.

- **Addresses are pinned per pod** and must stay outside the FRITZ!Box DHCP range (`.10`-`.60`) and
  the Cilium LoadBalancer pool (`.130`-`.240`). In use: `10.0.0.250` home-assistant,
  `10.0.0.251` esphome. The Service LoadBalancer IPs are unaffected.
- **`sbr` in the network definition is load-bearing.** A macvlan child cannot reach its own parent
  host, and that host answers every LoadBalancer IP (blocky, mosquitto, ...). Without `sbr` the pod
  would route all of `10.0.0.0/24` out of the macvlan and lose those services; with it only traffic
  sourced from the LAN address uses the second interface.
- **A pod created before Multus is running gets no second interface**, and nothing retries it. After
  (re)installing Multus, check the pod's `k8s.v1.cni.cncf.io/network-status` annotation lists
  `kube-system/lan`, and delete the pod if not.
- Home Assistant only uses the new interface for zeroconf once it is ticked under Settings ->
  System -> Network; its automatic choice picks the default-route interface only.
- Pods resolve through CoreDNS, which forwards to the node's nameservers (`1.1.1.1`, `8.8.8.8`),
  so `*.fritz.box` names do not resolve in the cluster.

## Storage: miroir

DRBD-based CSI, StorageClass `miroir-local`, `VolumeBindingMode: WaitForFirstConsumer`.

**A `Restore`'s claiming PVC will not bind, and its populator will not run, until a pod tries to
mount it.** The Longhorn-era "hold replicas at 0 while the restore completes" pattern stalls
forever. Sequence: PVC exists (`Pending` is fine) -> scale the Deployment to 1 -> the scheduling
attempt triggers the populator -> PVC binds -> pod starts.

**Concurrent restores can drop the populator handoff.** One PVC's staging PVC binds and its populate
job completes, but the final handoff never fires, with no errors logged. Tell: the stuck PVC lacks
`pv.kubernetes.io/bind-completed` that a working sibling has. Fix:
`kubectl -n miroir-system rollout restart deploy/miroir-controller` (binds within ~90 s). Don't
delete and recreate the PVC; that restarts the whole restore for nothing.

## Backups: Backblaze B2

Two buckets in `eu-central-003`: `kopiur` (Kopia repository) and `cnpg-gismo2004` (barman archives
of the CNPG databases).

**Every bucket needs a lifecycle rule that actually deletes hidden files.** B2 buckets default to
"keep all versions": a delete only hides the file and it stays billed. Barman and Kopia both
expect deletes to free space. `cnpg-gismo2004` had no rule until 2026-09-17 and had accumulated
52.6 GB of deleted-but-kept versions against 15.6 GB live, growing by the full ~2.5 GB daily
upload; earlier manual purges there (e.g. the immich prefix on 2026-09-01) freed nothing billed.
Both buckets now carry `daysFromHidingToDeleting: 1`. The rule lives in B2, not in this repo, so set
it on any new or recreated bucket (B2 console: Lifecycle Settings -> "Keep only the last version",
or `b2_update_bucket`). When judging bucket size, count hidden versions (`b2_list_file_versions`),
not just what `b2 ls` or barman reports.

## Backups: Kopiur

Backup wiring is either the shared `components/kopiur/backup` component or hand-rolled
`snapshotpolicy.yaml`/`snapshotschedule.yaml` for what the component can't express:

- **Multi-PVC apps** (`unmonitarr`, `vdf`) get one policy per PVC. **Never
  combine PVCs in one policy's `sources: [...]`**: everything is filed under `sources[0]`'s path
  and the rest become unrestorable.
- **Mover identity must be the UID the app writes as.** Restored files are owned by the mover's
  UID; a root mover without `privilegedMode` restores every file as `0:65532` mode 644, which an
  app running as 568 cannot write. No namespace carries `privileged-movers` any more:
  `home-assistant` and `esphome` moved to 568 on 2026-09-24.

**Kopia's own per-source policy can carry exclusions that git does not show.** Kopiur only adds
ignore rules to it, never removes them, so rules set by hand or by an older manifest survive, also
across the cluster migration. `esphome` silently excluded its `.device-builder.json` and peer-link
key this way until 2026-09-24. A snapshot's `stats.excludedFileCount` gives it away; inspect with
`kopia policy show <policy>@<namespace>:/pvc/<claim>` and remove with `--remove-ignore`.

`ClusterRepository/default` sets `scheduleDefaults` (`Europe/Vienna`, `jitter: 6h`) and
`concurrency.maxConcurrentJobs: 3`; schedules inherit both, so don't repeat the jitter per app.

**The snapshot identity is `<policy-name>@<namespace>:/pvc/<claim-name>`, all three parts.**
`KOPIUR_NAME` and `KOPIUR_CLAIM` are separate variables because they often differ (policy
`calibre`, claim `calibre-config`). Changing any part computes a different identity, and a `Restore`
with the default `onMissingSnapshot: Continue` then **silently populates an empty PVC**. For any
rename: pin `Restore.spec.source.identity` to the old values, set `onMissingSnapshot: Fail`, verify
real content, only then switch back to `fromPolicy` and rename in a follow-up commit.

**Landmine: converting an app from standalone backup files to the shared component can make Flux
delete and recreate the live objects in the same push.** The first apply pass rendered only the
literal resources, so prune deleted the live `SnapshotPolicy`/`SnapshotSchedule`/`Restore`/PVC; the
next reconcile recreated them and Kopiur re-adopted the snapshots, but **the live PVC sits
`Terminating`**, held only by `pvc-protection` while its pod runs, and deletes for real on the next
pod restart. After such a commit, check `kubectl get pvc -A | grep -i terminating`. If stuck: scale
to 0 (the PVC deletes), reconcile (recreated, `Pending`), scale to 1 (restore runs), verify content.

### Retiring an app without stranding its backup data

```
Snapshot.spec.deletionPolicy              Delete | Retain | Orphan
  policy-produced Snapshots here carry Delete; discovered ones are forced to Retain
ClusterRepository.spec.onNamespaceDelete  Orphan | Delete   -- ours is Orphan
```

**Order matters.** While the app and its `SnapshotPolicy` still exist, delete that policy's
`Snapshot` CRs (their kopia snapshots go with them), _then_ remove the app from git. The nightly
full maintenance (03:00, `Maintenance/default`) reclaims the blobs; confirm with
`status.full.lastContentReclaimedBytes`.

**The wrong order strands the data permanently.** Removing the app first orphans its kopia data; a
later catalog scan rediscovers it as `phase: Discovered`, forced to `deletionPolicy: Retain`, and
from then on only the kopia CLI can delete it. Renames do the same. **Orphans never age out:**
retention is enforced by the owning policy, so an orphan pins its blobs indefinitely and full
maintenance keeps reporting `0` reclaimed bytes.

**`onNamespaceDelete` must stay `Orphan`.** `Delete` would turn a stray `kubectl delete ns` or a bad
prune (see the landmine above) into unrecoverable data loss. Orphaned data is the recoverable
failure mode, and it is what lets `docs/MIGRATION.md`-style restores reach retired history.

**Before deleting a `Discovered` snapshot, compare its full identity** (`username` = policy name
_and_ `sourcePath` = PVC name) against every live `SnapshotPolicy` in that namespace. A live PVC can
keep an old name while the policy writing to it was renamed (`vdf` -> `vdf-config`), so a matching
PVC name alone proves nothing. The repository has no `spec.catalog.periodicRefresh`, but a rebuild
or re-bootstrap rescans and rediscovers every retired identity still in the repository.

## Backups: CNPG / barman, a separate system

`home-assistant`, `mealie`, `photoview` and `photoview-incoming` run CloudNativePG clusters that
back up via barman to `s3://cnpg-gismo2004/<app>/`, **not** covered by Kopiur. A Kopiur-only
restore of one of these apps brings back its config with an empty database. `immich` deliberately
has no database backup (see the comment in its `postgres.yaml`). Read the comment in each `Cluster`
manifest before touching `spec.backup`/`externalClusters`.

- **A cluster recovering via `bootstrap.recovery` must use a different
  `spec.backup.barmanObjectStore.serverName` than its `externalClusters` source**, or the restore
  pre-flight refuses with `Expected empty archive`. The same `destinationPath` is fine.
- Compression is `gzip`, deliberately: bzip2 pegged a core compressing slower than the ~3.4 MB/s B2
  uplink for a ratio only marginally better. CNPG offers no zstd.
- `monitoring.enablePodMonitor` is unset by default; check it first when a CNPG dashboard is empty.
- Archive volume follows database write volume. Home Assistant's recorder is by far the largest
  source (about 6 GB of WAL a day before compression); a sensor updating every second shows up
  directly in B2.

## HelmRelease status can lie

A `HelmRelease` can report `Stalled: MissingRollbackTarget` for hours after its Deployment
recovered: helm-controller stops retrying when no revision ever succeeded. If the Deployment has
been `Available` longer than the release claims to have failed, it's stale;
`flux reconcile helmrelease <name> -n <ns> --reset` makes it retry (without `--reset` it does not).

## The arr suite: `TrustedNetworks` gates the reverse-proxy auth exemption

`sonarr`, `radarr`, `lidarr` and `prowlarr` run `*__AUTH__REQUIRED: DisabledForLocalAddresses`,
which needs the app to trust `envoy-internal`. Servarr's `TrustedNetworks` key is **fail-closed**:
a request carrying `X-Forwarded-For` from an unlisted sender loses the local-address exemption
without the forwarded address being evaluated. All four set
`<APP>__SERVER__TRUSTEDNETWORKS: 172.16.0.0/24` (the pod CIDR).

**The env var is runtime-only:** `config.xml` shows `<TrustedNetworks></TrustedNetworks>` even when
it works, and nothing but the HelmRelease comment explains it. Commit `de2bbd5` once removed it as
"redundant TrueCharts env vars"; don't repeat that.

Diagnose by varying only the forwarded address and comparing siblings:

```
kubectl -n media exec deploy/sonarr -c app -- \
  curl -s -o /dev/null -w '%{http_code}\n' -H 'X-Forwarded-For: 10.0.0.61' \
  http://radarr.media.svc.cluster.local:7878/
```

Healthy apps answer differently: `Basic` auth (sonarr) returns `401`, `Forms` (radarr/lidarr)
`302`. `*__AUTH__METHOD: ""` is a no-op, not "auth disabled": it falls through to `config.xml`.

## Renovate

`.renovaterc.json5` extends `home-operations/renovate-presets`. **Auto-merge is the default** for OCI
digests and for minor/patch of apps, Actions, presets and mise tooling. Majors never auto-merge and
wait out a 3-day `minimumReleaseAge`.

**The no-automerge denylist is scoped by recoverability:** can a bad update be undone with a git
revert? Stays manual: `cilium`/`coredns` (nothing resolves, Flux can't pull), `flux-operator`/
`flux-instance` (breaks the revert mechanism), `multus`/`cni-plugins` (wrap the CNI of every new
pod), `miroir` (a revert doesn't unbreak an unmountable volume), `cloudnative-pg`, `talos`/`kubelet`
(single node), `kopiur` (breaks silently until a restore) and `app-template` (one chart behind most
apps). Projects like cilium and coredns ship features in "minor" releases. Breaking ingress or
telemetry (`cert-manager`, `envoy`, `external-dns`, `metrics-server`) is revertable, so those
auto-merge.

**CI only proves the manifests render** (`flate`); auto-merge means "the YAML is valid", not "the app
works". The revert path is the real safety net.

**A rule only applies if its `matchUpdateTypes` covers the update.** `digest` is not `patch`, so a
`["minor", "patch"]` rule silently skips digest bumps (`Automerge: Disabled by config`, no error).
Trace an unexpectedly unmerged PR rule by rule against its datasource _and_ update type.

Grafana dashboards are `GrafanaDashboard` CRs referencing a URL so Renovate can bump them: the
preset handles grafana.com IDs, a hand-written `customManagers` regex handles `blocky` and
`cloudnative-pg`. **Any new regex manager must set `autoReplaceStringTemplate` explicitly**, and be
dry-run against a real line first: without it Renovate replaces the whole match with the bare
version (a first attempt turned a dashboard URL into `url: v0.34.0`), and
`renovate-config-validator` does not catch that.

## Talos

Machine config is topf-rendered (`talos/topf.yaml` + fragments). The node sits on `bond0`
(`10.0.0.48`, API VIP `10.0.0.120`). The BOOT partition is 2.2 GB, large enough for two boot
generations with the nvidia extension; the old cluster's 1 GB partition was what forced the rebuild
(narrative in `docs/MIGRATION.md`).

**The Talos release caps the Kubernetes version, and Renovate doesn't know it.** Each Talos minor
supports Kubernetes up to its own default version (Talos 1.13 stopped at 1.36; 1.37 needed 1.14).
Renovate tracks `ghcr.io/siderolabs/kubelet` as a plain image and will propose versions the running
Talos cannot run. **Always upgrade Talos first, then Kubernetes**, and dry-run a proposed version
before merging:

```
talosctl -n <node-ip> upgrade-k8s --to=<version> --dry-run
```

The kubelet version lives in two files sharing one `# renovate:` marker: `talos/topf.yaml`
(`kubernetesVersion:`) and the tuppr `KubernetesUpgrade` CR in
`kubernetes/apps/system-upgrade/tuppr/upgrades/kubernetesupgrade.yaml`. If a bump fails:

- talosctl refuses before changing anything, but `topf.yaml` is left pointing at a version this
  Talos can't run, and the next `just talos apply` would push it. Revert both files.
- **tuppr latches `phase: Failed`** and logs `Kubernetes upgrade in terminal state, skipping` forever,
  while the `tuppr-upgrades` Kustomization sits `Reconciliation in progress` on a health check that
  can't pass. Both clear only when the CR's `spec` changes in git; a live `kubectl` edit is
  reverted.
- The job's pods are cleaned up quickly. The surviving evidence is `status.history[]` on the CR
  (timestamps in **UTC**) and the controller log (`+02:00`).
