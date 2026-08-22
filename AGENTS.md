# Agent Guidelines for home-ops

## Mandatory Cluster Instructions & Architecture

Always consult and adhere to [`CLAUDE.md`](file:///home/gismo/git_repos/home-ops/CLAUDE.md) for all changes and operations in this repository.

`CLAUDE.md` documents authoritative, cluster-specific facts and operational rules:

1. **Tooling & Environment**: Tool versions are pinned via `.mise/config.toml`. Run commands using `eval "$(mise activate bash)"`.
2. **Formatting & Pre-commit**: All YAML/JSON/Markdown changes must be formatted with `oxfmt` (`eval "$(mise activate bash)" && oxfmt <paths>`).
3. **Storage & Miroir**: Miroir CSI uses local DRBD on ext4/XFS — do not assume ZFS.
4. **Kopiur Backups**: Follow exact naming and policy rules defined in `CLAUDE.md`. Use `wait: false` on app Kustomizations.
5. **Namespaces**: `apps/default/` is a folder grouping; each app uses its own dedicated namespace (except the shared `media` group).
6. **SOPS Encryption**: Never commit decrypted secrets. Always encrypt using `sops -e -i <file>`.
