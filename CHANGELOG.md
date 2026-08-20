# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Versioning policy

The Python package, VS Code extension, and MCP Registry manifest are released in
lockstep from the version in `src/djobs/__init__.py`. The project is pre-1.0, so
public interfaces may still change between minor versions. Entries below use
`[core]`, `[ext]`, `[docs]`, or `[release]` to identify the affected surface.

## [Unreleased]

### Added
- `[core]` Add optional `djobs project doctor|init|status|next` commands that bridge to an installed ARUN durable project-state engine without making ARUN a required dependency or starting another executor.
- `[docs]` Document the memory / coordination / completion product layers and the ARUN Project Mode authority boundary.

### Changed
- `[docs]` Reposition the README, package metadata, and GitHub Pages landing page around local project memory, explicit cross-agent coordination, and optional verified project completion.

## [0.20.1] - 2026-08-05

### Fixed
- `[release]` Harden advisory receipt verification (#57)


## [0.20.0] - 2026-08-05

### Added
- `[release]` Add advisory-only host contract v1 (#55)


## [0.19.0] - 2026-07-31

### Added
- `[release]` Harden storage, recovery, diagnostics, and privacy (#53)


## [0.18.4] - 2026-07-26

### Fixed
- `[release]` Harden workspace trust and host config updates (#51)


## [0.18.3] - 2026-07-26

### Fixed
- `[release]` Production hardening and documentation calibration (#48)


### Fixed
- `[core]` Pause now suppresses automatic capture, snapshots, session capsules, first-call bootstrap, and workspace recovery without blocking manual inspection or deletion.
- `[core]` Recovery reads prior memory before persisting the current prompt, preventing the current request from ranking as its own history.
- `[core]` Bounded session-capsule storage preserves structured goal, progress, failure, next-step, and source fields.

### Changed
- `[release]` Pull requests run real compatibility, package, extension, database, and installed-wheel gates before merge instead of no-op compatibility placeholders.
- `[docs]` Public benchmark copy now describes a synthetic payload-size regression fixture and its assumptions instead of claiming a fixed percentage of provider-token savings.

## [0.18.2] - 2026-07-25

### Changed
- `[release]` Make local agent memory the primary djobs product (#42)


## [0.18.1] - 2026-07-24

### Performance
- `[release]` Add shared preflight and layered CI (#40)


## [0.18.0] - 2026-07-24

### Added
- `[release]` Add layered recovery tiers and verified-task efficiency (#38)


### Added
- `[core]` Added layered `sync_workspace` recovery tiers: a minimal `resume` capsule for normal continuation, compact `evidence`, and full `audit` detail. The MCP defaults to `resume` while direct Python callers retain the prior audit-shaped default for compatibility.
- `[core]` Extended `djobs gain` with verified-task efficiency metrics, including first-pass verified rate, repair attempts, average attempts, cycle-time proxy, and estimated context tokens per verified task.

### Changed
- `[core]` Session capsule metadata now remains structurally available to recovery instead of forcing agents to parse a single summary string.
- `[core]` Reuse the process-local SQLite queue for repeated calls to the same database, while safely closing the old handle when configuration changes.

### Fixed
- `[core]` Normalize WSL `/mnt/<drive>/...` paths to the matching Windows repository identity even when djobs itself runs on Windows.
- `[release]` Close benchmark SQLite handles before temporary-directory cleanup so the recovery benchmark is reproducible on Windows.

## [0.17.2] - 2026-07-24

### Fixed
- `[release]` Extract GitHub Release notes deterministically (#28)
- `[release]` Approve gated release CI after completion (#30)
- `[release]` Approve release PR CI inside the release job (#34)


## [0.17.1] - 2026-07-24

### Fixed
- `[release]` Synchronize automatic releases through protected main (#25)


## [0.17.0] - 2026-07-24

### Added
- `[release]` Add automatic multi-platform release pipeline (#23)


## [0.16.2] - 2026-07-23

### Fixed
- `[release]` Include migration files in sdists (#20)


## [0.16.1] - 2026-07-23

### Fixed
- `[release]` Fix PyPI release workflow (#18)


## [0.16.0] - 2026-07-23

### Added
- `[release]` Add zero-touch startup integration (#16)


## [0.15.0] - 2026-07-23

### Added
- `[release]` Add automatic checkpoint hooks (#15)


## [0.14.0] - 2026-07-23

### Added
- `[release]` Add project memory and cross-agent handoff (#13)


## [0.13.0] - 2026-07-23

### Added
- `[release]` Add query-aware memory recall (#11)


## [0.12.0] - 2026-07-23

### Added
- `[release]` Add passive repository memory (#10)


## [0.11.0] - 2026-07-22

### Changed
- `[release]` Reposition djobs as local agent memory (#8)


## [0.10.0] - 2026-07-22

### Added
- `[release]` Add VS Code extension (#7)


## [0.9.0] - 2026-07-22

### Added
- `[release]` Add VS Code Marketplace packaging (#6)


## [0.8.0] - 2026-07-22

### Added
- `[release]` Add benchmark and gain reporting (#5)


## [0.7.0] - 2026-07-22

### Added
- `[release]` Add setup and doctor commands (#4)


## [0.6.0] - 2026-07-22

### Added
- `[release]` Add lifecycle capture adapters (#3)


## [0.5.0] - 2026-07-22

### Added
- `[release]` Add compact coding MCP server (#2)


## [0.4.0] - 2026-07-22

### Changed
- `[release]` Make local SQLite the default backend (#1)


## [0.3.0] - 2026-07-22

### Added
- `[core]` Add correlation IDs, revision cursors, and resume-session recovery.

## [0.2.0] - 2026-07-22

### Added
- `[core]` Add durable queue leases, heartbeats, delayed jobs, and crash recovery.

## [0.1.0] - 2026-07-22

### Added
- `[core]` Initial SQLite-backed durable job queue with MCP integration.
