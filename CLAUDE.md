# CLAUDE.md

Guidance for Claude Code (or any agent) working in this repository. Treat this as authoritative
for how this specific cluster behaves — it documents load-bearing facts and known landmines that
aren't obvious from reading the manifests alone.

## What this is

A FluxCD GitOps repo for a bare-metal-equivalent (single Proxmox VM) Talos Linux Kubernetes
homelab cluster. Every cluster change is a git commit to `origin/main` — Flux reconciles the live
cluster to match the repo, not the other way around. Modelled on
[onedr0p/home-ops](https://github.com/onedr0p/home-ops) and bootstrapped from
[siderolabs/cluster-template](https://github.com/siderolabs/cluster-template); apps and data were
migrated from a prior cluster (`gismo2004/HomeCluster`, now historical-only — see
`docs/MIGRATION.md`).

There is no traditional build/lint/test suite. "Correct" means valid Kubernetes/Kustomize/Helm
YAML that Flux can reconcile without errors — use `kustomize build`, `kubeconform`, and CI's
`flate` check accordingly (see "Validating a change" below).

## Environment & tooling

Tool versions are pinned via `mise` (`.mise/config.toml`, **not** the empty top-level `mise.toml`
— that's a vestigial file from the cluster-template scaffold with nothing in it; `.mise/config.toml`
is what's actually loaded, confirmed via `mise config`). Run `mise install` once per checkout.
`.mise/config.toml`'s `[env]` block exports `KUBECONFIG`, `SOPS_AGE_KEY_FILE`, `SOPS_CONFIG`, and
`TALOSCONFIG` from repo-relative paths — no `direnv` needed, mise handles it.

Current pins worth knowing: `flux2` 2.9.4, `kubectl`/`talosctl` tracking the live cluster
(currently k8s v1.36.4, Talos v1.13.9), `kustomize` 5.8.1, `sops` 3.13.3, `helm` 4.2.4.

Common commands (`just`, loading `bootstrap/mod.just`, `kubernetes/mod.just`, `talos/mod.just` as
modules — run `just --list --list-submodules` to see the full menu):

- `just kube reconcile` — force Flux to pull; a GitHub webhook triggers this automatically on
  push, so this is only for when reconciliation genuinely seems stuck.
- `just talos diff` — preview pending Talos machine-config changes; `just talos apply`/
  `apply-node <node>` to apply (shows a diff and asks first), `just talos upgrade-node <node>` for
  a Talos upgrade, `just talos upgrade-k8s` for a Kubernetes upgrade.
- `just bootstrap talos` / `just bootstrap apps` — the two-phase cluster bootstrap.
- `sops -d <file>` — decrypt a `*.sops.yaml`; never print/commit decrypted content.
- `kustomize build kubernetes/apps/<group>/<app>/app` — render one app's manifests locally before
  trusting a change.
- Pre-commit (`lefthook`, `.lefthook.toml`) auto-formats staged YAML/JSON/Markdown with `oxfmt`,
  formats staged `.justfile`s and mise config, and runs `zizmor` against GitHub Actions workflows.
  **It does not auto-encrypt sops files** — that's a manual `sops -e -i <file>` step (the
  `path_regex` rules in `.sops.yaml` only take effect when `sops` is actually invoked, not on
  save). If a hook fails with `oxfmt: not found`, mise's shims aren't on `$PATH` — run
  `export PATH="$HOME/.local/share/mise/shims:$PATH"` rather than skipping the hook.

## Repository layout

```
kubernetes/
  flux/cluster/ks.yaml     — the root Kustomization ("cluster-apps"), applies ./kubernetes/apps
  apps/<group>/<app>/
    ks.yaml                — the Flux Kustomization, points at ./app, sets targetNamespace
    app/kustomization.yaml — lists that app's resource files
    app/helmrelease.yaml   — almost always app-template, via chartRef → app/ocirepository.yaml
  components/
    sops/                  — the cluster-secrets sops component (some namespaces pull it in;
                              see "Variable substitution" below — its one key is currently unused)
    kopiur/backup/         — shared backup wiring, see "Backups: Kopiur" below
bootstrap/                 — helmfile-driven CRD/core-component bootstrap (crds.yaml, apps.yaml)
talos/                     — topf-rendered machine config (topf.yaml + fragments)
docs/MIGRATION.md          — the HomeCluster→home-ops rebuild runbook (historical, but the
                              per-app landmines it documents — identity, dual-serverName — are
                              still live facts, not just history)
```

**`apps/default/` is a directory grouping, not the literal Kubernetes `default` namespace.** It's
named after cluster-template's own convention. Only the `echo` test app actually deploys into the
real `default` namespace — every other app under `apps/default/` gets `targetNamespace: <app-name>`,
its own dedicated namespace, in its `ks.yaml`.

**Known exception, not yet resolved:** six apps (`jellyfin`, `lidarr`, `prowlarr`, `radarr`,
`sabnzbd`, `sonarr`) share `targetNamespace: media` instead of getting their own namespace —
inherited from the old TrueCharts-era stack. There's no technical requirement forcing this (the
shared NFS downloads mount isn't namespace-scoped, cross-app references already use full FQDNs
like `jellyfin.media.svc.cluster.local`). If this is ever split into per-app namespaces, it is
**not** a simple rename — see "Backups: Kopiur" below for why, and follow the same
identity-preserving procedure documented in `docs/MIGRATION.md` for the original rebuild.

## Variable substitution — mostly unused; check before assuming it's live

`kubernetes/components/sops` is a Kustomize `Component` some apps pull in via
`components: [../../components/sops]`, which would give `postBuild.substitute`/`substituteFrom`
access to the `cluster-secrets` sops Secret. That Secret currently defines exactly one key,
`SECRET_DOMAIN` (`gismo2004.cc`) — **and nothing in the repo actually references
`${SECRET_DOMAIN}`.** Every hostname across all 42 files that have one (checked repo-wide) is a
literal `gismo2004.cc` string, not a substituted variable. This is **not** the old HomeCluster
pattern of one monolithic ~57-key secret feeding widespread `${VAR}` substitution — that pattern
was deliberately abandoned; most of what used to live there (LoadBalancer IPs, NFS paths, cluster
facts) are literal values here, and apparently the domain ended up literal too somewhere along the
way rather than staying substituted. Don't assume a new app needs `${SECRET_DOMAIN}` just because
older docs/examples show it — check a recent sibling app's manifest for the current convention
(currently: hardcode the literal hostname). Genuinely sensitive values (DB passwords, API tokens)
get their own per-app `app/secret.sops.yaml`, which is the pattern that's actually load-bearing.

**Never `kubectl apply -f` a repo file containing unresolved `${...}` placeholders** — it writes
the literal placeholder string into the live object. Push to git and let Flux substitute, or use
`kubectl patch` against already-substituted fields for narrow, temporary debugging only.

## Per-app patches go in `app/kustomization.yaml`, never in `ks.yaml`'s `spec.patches`

The root `cluster-apps` Kustomization (`kubernetes/flux/cluster/ks.yaml`) patches a `spec.patches`
block (the shared HelmRelease install/upgrade defaults) onto **every** child Kustomization. That
merge **replaces the list wholesale**, so anything a child `ks.yaml` puts in its own
`spec.patches` is silently dropped — no error, no warning, the field just isn't there on the live
object. Check with `kubectl -n default get kustomization <app> -o yaml` if in doubt: if the only
entry under `spec.patches` is the HelmRelease one, yours was eaten.

Patch at the Kustomize layer instead — a `patches:` block in the app's own
`app/kustomization.yaml`. It applies to component-generated resources too (verified against
`components/kopiur/backup`), and unlike the Flux-level version it's visible in a local
`kustomize build`. Target by `group`+`kind` only: `postBuild.substitute` runs _after_ the build,
so at patch time the object is still literally named `${KOPIUR_NAME}` and a name filter never
matches. `edgetx`'s Kopiur cache exclusion sat dead this way from commit `761dea5` until it was
found and moved on 2026-08-24.

## Workflow: git+Flux for configuration, kubectl for state changes

**Configuration** (a resource's desired spec) belongs in git, applied by Flux. **State changes**
(scaling a Deployment, triggering an existing CR, deleting a stray cache PVC) are fine directly
via `kubectl`. A push triggers reconciliation automatically via webhook — no need to
`flux reconcile` after every push, only if reconciliation genuinely seems stuck.

A live `kubectl patch`/`apply` on something that _is_ configuration gets silently reverted on the
next reconcile of that Kustomization, with no error, because git is the source of truth Flux
restores to.

## Storage: miroir

DRBD-based CSI (`miroir.home-operations.com`), StorageClass `miroir-local`,
`VolumeBindingMode: WaitForFirstConsumer`.

**A `Restore`'s claiming PVC will not bind, and its populator will not run, until a pod actually
tries to mount it.** The old "hold replicas at 0 while the restore completes" pattern used on the
previous cluster's storage driver (Longhorn) **stalls forever** on miroir — WFC means nothing
happens until there's a consumer. The correct sequence for a from-scratch or post-delete restore
is: PVC exists (`Pending` is expected and fine) → scale the Deployment to 1 → the pod's scheduling
attempt triggers the CSI populator → PVC binds once the restore completes → pod starts.

**Known failure mode: a dropped populator handoff under concurrent restores.** Restoring two PVCs
at the same time can leave one of them stuck — its "prime" staging PVC provisions and binds fine,
its populate job completes fine, but the final handoff to the real claiming PVC never fires. Zero
errors logged anywhere in `miroir-controller` or the agent. Tell: compare
`pv.kubernetes.io/bind-completed` between the stuck PVC and a sibling that worked — the stuck one
is missing it. Fix: `kubectl -n miroir-system rollout restart deploy/miroir-controller` (single
replica, safe to restart, doesn't touch the already-successful sibling). Binds within ~90s of the
restart. Don't delete/recreate the PVC to "retry" — that just restarts the whole restore for no
reason; the data staged in the prime PVC was never at risk.

## Backups: Kopiur

Every app's backup wiring is either the shared `components/kopiur/backup` component (16 apps as
of writing — `blocky`, `calibre`, `edgetx`, `iceagent`, `jellyfin`, `krokiet`, `lidarr`, `mealie`,
`orcaslicer`, `oscam`, `prowlarr`, `radarr`, `sabnzbd`, `sonarr`, `tasmoadmin`, `tasmobackup`) or
hand-rolled `snapshotpolicy.yaml`/`snapshotschedule.yaml` files for apps the component can't
express: multi-PVC apps (`kavita`, `mosquitto`, `unmonitarr`, `vdf` — each backs up two
independent PVCs; **never combine them into one policy's `sources: [...]`**, a multi-source policy
files everything under `sources[0]`'s path and the others become unrestorable) and apps needing a
non-default mover identity (`home-assistant`, `esphome` run root movers; `jdownloader2` needs the
`privileged-movers` namespace annotation).

**The snapshot identity is `<policy-name>@<namespace>:/pvc/<claim-name>` — all three parts.** When
using the shared component, `KOPIUR_NAME` and `KOPIUR_CLAIM` are two separate substitution
variables specifically because they frequently differ (policy `calibre` backs up claim
`calibre-config`). Changing any one of the three — policy name, namespace, or PVC name — computes
a _different_ identity, and a `Restore` against it finds nothing. With the default
`onMissingSnapshot: Continue` this **silently populates an empty PVC** rather than failing. This is
why the six `media`-namespace apps can't be casually moved to their own namespaces (see "Repository
layout" above) and why any future rename needs the same staged procedure `docs/MIGRATION.md` used:
pin `Restore.spec.source.identity` to the _old_ values explicitly, set `onMissingSnapshot: Fail`,
verify real content lands, only _then_ flip back to `fromPolicy` and rename in a follow-up commit.

**Landmine: converting an app from standalone backup files to the shared component can make Flux
delete-then-recreate the live objects in the same push.** Observed converting `tasmoadmin`/
`tasmobackup`: kustomize-controller's first apply pass within that reconcile only rendered the
literal-resource objects (HelmRelease, OCIRepository), missing the four component-sourced ones, so
prune/GC ran against an incomplete build and deleted the live `SnapshotPolicy`/`SnapshotSchedule`/
`Restore`/`PersistentVolumeClaim`. The very next reconcile (~4s later) rebuilt correctly and
recreated everything with the same identity, and Kopiur's auto-adoption reattached all existing
snapshots with no data loss — but the **live PVC gets a `deletionTimestamp` and sits stuck
`Terminating`**, held open only by the `pvc-protection` finalizer as long as its pod keeps running.
If that pod is ever restarted afterward for any unrelated reason, the finalizer releases and the
volume actually deletes, forcing an unplanned restore at an uncontrolled time.
After any commit that adds `spec.components:` to a Kustomization that didn't have it while also
removing the previously-inline resource files it replaces, immediately check
`kubectl get pvc -A | grep -i terminating` — don't assume a clean push means a clean conversion.
If caught stuck: scale the Deployment to 0 (releases the finalizer, PVC actually deletes), force a
reconcile (recreates the PVC from git, `Pending` per the WFC note above), scale back to 1
(triggers the restore), verify real content — not just `Bound` status.

**Discovered/orphaned snapshots are a recurring cleanup, not a one-time fix.** A `Snapshot` CR with
`status.phase: Discovered` means the kopia repository holds history under an identity nothing
currently owns (a retired policy name, a renamed PVC, a pre-split combined policy). These are
CRD-forced to `deletionPolicy: Retain`, so deleting the CR is safe for the Kubernetes side, but the
underlying kopia data in B2 is never reclaimed by it, and — because this `ClusterRepository` has no
`spec.catalog.periodicRefresh` — a fresh catalog scan (a rebuild, a repository re-bootstrap) will
rediscover the same retired identities again. **Before deleting one, compare its full identity
(`username`/policy-name _and_ `sourcePath`/PVC-name) against every currently-live
`SnapshotPolicy` in that namespace — matching on PVC name alone is not enough.** A live PVC can
still exist under an old name (e.g. `vdf-app-template-config`) while the _policy_ name that wrote
history to it has since changed (`vdf` → `vdf-config`); the orphan's `username` field is what tells
these apart, not whether a same-named PVC exists.

## Backups: CNPG / barman — a separate system, easy to miss

`hass`, `mealie`, `photoview` run CloudNativePG clusters that back up via barman to
`s3://cnpg-gismo2004/<app>/` — **not** covered by any Kopiur `SnapshotPolicy`. A Kopiur-only
backup of one of these apps restores its config with an empty database. Every CNPG `Cluster`
manifest carries a comment on this; read it before touching `spec.backup`/`externalClusters`.

**A CNPG cluster recovering via `bootstrap.recovery` must use a _different_
`spec.backup.barmanObjectStore.serverName` than its `externalClusters` recovery source**, or the
restore job's own `barman-cloud-check-wal-archive` pre-flight refuses with
`Expected empty archive` — it exists specifically to stop two cluster timelines writing into the
same catalog. Same `destinationPath` is fine (barman namespaces by serverName underneath it).

`monitoring.enablePodMonitor` defaults to unset (effectively `false`) — if a CNPG dashboard shows
no data for an app, check this before assuming a scrape config problem.

Compression is `gzip`, not the CNPG-common default of `bzip2` — deliberately, measured against
this specific link: B2 upload here tops out at ~3.4 MB/s, bzip2 only compresses at ~5.7 MB/s (so
it was pegging a full CPU core just to feed a link it could barely keep ahead of) for a ratio only
4 points better than gzip's ~22.8 MB/s. CNPG's compression enum is `bzip2|gzip|lz4|snappy` — no
zstd option exists.

## HelmRelease status can lie — check the age of the failure, not just its presence

A `HelmRelease` can report `Stalled: MissingRollbackTarget` / `Ready=False` for hours after the
underlying Deployment fully recovered on its own — helm-controller stops retrying once neither
Helm revision succeeded (no rollback target exists), and just re-reports the cached failure
forever. `metadata.generation == status.observedGeneration` staying equal over time is the tell
that it's stale, not actively broken. Before treating a non-Ready HelmRelease found during an
unrelated sweep as urgent: check its `lastTransitionTime` and cross-check the actual
Deployment/pod status directly. If the Deployment's been `Available`/`Running` for longer than the
HelmRelease claims to have failed, it's stale — `flux reconcile helmrelease <name> -n <ns> --reset`
clears it (asks helm-controller to actually retry; a bare `flux reconcile` without `--reset` does
not).

## Renovate

`.renovaterc.json5` extends `home-operations/renovate-presets`. **Auto-merge is the default**:
OCI digests, plus minor/patch for apps, GitHub Actions, Renovate presets and mise tooling. Majors
never auto-merge and additionally wait out a 3-day `minimumReleaseAge`.

**The denylist that blocks auto-merge is scoped by recoverability, not by category.** Because every
change here is a git commit Flux applies, the question for any unattended merge is "can a bad one
be walked back with a revert?" — for almost everything it can, within a minute. The packages that
stay manual are the ones where it can't: `cilium`/`coredns` (Flux can't pull, nothing resolves),
`flux-operator`/`flux-instance` (breaks the mechanism that would apply the revert), `miroir`
(CSI/DRBD on 0.11.x — a revert doesn't unbreak an unmountable volume), `cloudnative-pg`,
`talos`/`kubelet`, `kopiur` (a break is silent until a restore is needed) and `app-template` (one
chart behind ~26 apps). Note these projects don't use semver the way applications do — a cilium or
coredns "minor" is a feature release, which is how an unrestricted auto-merge once took cilium
1.18.6 → 1.20.0 unattended on the prior cluster. Things like `cert-manager`, `envoy`,
`external-dns`, `metrics-server` were on this list and were deliberately removed on 2026-08-27:
breaking them costs ingress or telemetry, not the cluster or its data.

**What CI actually gates.** The automerge rules set `ignoreTests: false` for anything touching
`kubernetes/**`, but the only check is `flate` — it proves the manifests render and the dependency
graph resolves, it never starts a container. Auto-merge here means "the YAML is valid", not "the
app works"; the real safety net is the revert path above.

**A rule only applies if some `matchUpdateTypes` actually covers the update.** `digest` is not
`patch` — a rule listing `["minor", "patch"]` silently skips digest bumps, which is why third-party
digest PRs (calibre, kavita) sat unmerged for days with `Automerge: Disabled by config` and no
error anywhere. When an unexpected PR won't auto-merge, trace it rule by rule against its datasource
_and_ its update type before assuming the denylist caught it.

Grafana dashboards are `GrafanaDashboard` CRs referencing a URL (grafana.com or an upstream repo),
not vendored JSON, specifically so Renovate can bump the pinned revision automatically — the
upstream `grafanaDashboards` preset handles grafana.com IDs; a hand-written `customManagers` entry
in `.renovaterc.json5` handles the two that ship in their own repo (`blocky`, `cloudnative-pg`),
pinned to a release tag via a `# renovate: datasource=... depName=...` marker comment.

**If you ever add another such hand-written regex `customManager`: always set
`autoReplaceStringTemplate` explicitly, and dry-run the regex+template against a real matching
line before trusting it.** Renovate's regex managers replace the _entire_ matched string with that
template, defaulting to just the bare new version if it's omitted. A first attempt at the
blocky/cloudnative-pg manager omitted it and produced a PR that would have replaced
`url: https://raw.githubusercontent.com/0xERR0R/blocky/v0.34.0/docs/blocky-grafana.json` with
`url: v0.34.0` outright — caught in review before merge, not by `renovate-config-validator` (which
only checks the config parses, not what a match produces).

## Validating a change before merge

- `kustomize build kubernetes/apps/<group>/<app>/app` — does it render at all. Note this won't
  resolve any `${KOPIUR_*}`-style component placeholders (that's Flux's `postBuild.substitute`,
  not Kustomize), so a build using `components/kopiur/backup` will still show literal `${...}` in
  the output — expected, not a failure.
- `kustomize build kubernetes/apps/<group>/<app>/app | kubeconform -strict -ignore-missing-schemas`
  — schema-valid against the actually-rendered output, not the raw per-file YAML.
- CI's `flate` workflow (`.github/workflows/flate.yaml`) runs `flate test all` against
  `kubernetes/flux/cluster` on every PR touching `kubernetes/**` — this is the closest thing to a
  real dry-run against Flux's own dependency graph; a clean `kustomize build` doesn't guarantee
  this passes.
- For anything touching Kopiur/CNPG backup identity, don't trust a `Succeeded`/`Completed` status
  alone — restore into a throwaway object and check real content, per the landmines above.

## Talos

Machine config is topf-rendered (`talos/topf.yaml` + fragments), same tooling as the prior
cluster. The rebuild that created this cluster was forced by the old cluster's 1 GB `BOOT`
partition (fixed at install time, too small for two boot-entry generations once the nvidia
extension was added) — this cluster's BOOT partition is 2.2 GB, confirmed large enough that
`v1.13.7 → v1.13.9` and the matching kubelet bump already went through cleanly post-rebuild. Full
rebuild narrative, the bootstrap CRD-ordering traps hit along the way, and the per-app restore
procedure are in `docs/MIGRATION.md`.
