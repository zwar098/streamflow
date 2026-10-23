# StreamFlow 2.8.0

This release promotes the complete tested `dev` branch to `main`, including the
features and fixes below. Changes are relative to **2.7.1**. Stable users should
update for the security fixes even if they do not use the new features.

### Added

- **Channel sorting and effective-profile filters ([#514](https://github.com/krinkuto11/streamflow/issues/514), [#516](https://github.com/krinkuto11/streamflow/pull/516)).** Sort the Channels list by channel number, natural channel name or assigned stream count in either direction. Filter by all profiles, a selected effective automation profile, or no configured profile, including inherited group assignments. Sorting and filtering apply before pagination, combine with existing search/group filters and preserve the saved Channel Order. Suggested by [@sm0keb0x](https://github.com/sm0keb0x).
- **Expandable Teamarr Preflight stream details ([#509](https://github.com/krinkuto11/streamflow/issues/509), [#510](https://github.com/krinkuto11/streamflow/pull/510)).** Open a preflight item's stream table to inspect individual status, measured resolution, frame rate, codecs, bitrate, quality scores and available score details. The view stays collapsed until opened, and sensitive source URLs and credentials are excluded. Suggested by [@ajama-1984](https://github.com/ajama-1984).
- **Concurrent Teamarr game checks ([#498](https://github.com/krinkuto11/streamflow/pull/498)).** The existing Concurrent Game Checks setting can run independent games in parallel, with separate progress and cancellation state and a shared probe budget. Provider/profile capacity limits, active-viewer protection and priority/FIFO queue ordering remain enforced. The default of one retains sequential operation. Contributed by [@meekju13391339](https://github.com/meekju13391339).
- **Unraid DockerMan template and installation guide ([#494](https://github.com/krinkuto11/streamflow/pull/494)).** Add editable persistent Appdata, port, user/group and optional CPU/GPU settings, plus installation/update guidance. Community Applications listing remains subject to submission and review.

### Fixed

- **Cleaner Teamarr Preflight lists ([#511](https://github.com/krinkuto11/streamflow/issues/511), [#512](https://github.com/krinkuto11/streamflow/pull/512)).** Excluded sports and leagues disappear from normal preflight views. An explicit Filtered view remains available for diagnosis. Suggested by [@sm0keb0x](https://github.com/sm0keb0x).
- **Monitoring regex composition ([#379](https://github.com/krinkuto11/streamflow/issues/379), [#468](https://github.com/krinkuto11/streamflow/pull/468)).** Preserve leading Python inline flags when combining multiple channel patterns for monitoring sessions. Reported by [@wredfearn](https://github.com/wredfearn).
- **Dispatcharr login bursts and throttling ([#454](https://github.com/krinkuto11/streamflow/issues/454), [#469](https://github.com/krinkuto11/streamflow/pull/469)).** Serialize credential login attempts, reuse refreshed tokens and honor numeric/date Retry-After headers and server throttle hints instead of retrying too early. Reported by [@firestaerter3](https://github.com/firestaerter3).
- **Responsive match-count scans.** Prepare and safety-check patterns once per channel. Rapid sorting/filter changes keep one scan in flight and skip superseded queued views, avoiding overlapping full-inventory scans.
- **SQL configuration authority.** Preserve authoritative restored SQL settings instead of overwriting them with stale legacy JSON during startup migration.
- **Heartbeat regression test.** Make the integration-test synchronization deterministic while retaining its lock, capacity, snapshot and redaction assertions.

### Security

- **Bounded regex mass edits ([#517](https://github.com/krinkuto11/streamflow/pull/517), [#518](https://github.com/krinkuto11/streamflow/pull/518)).** Preview and Apply prepare replacements in a disposable Python process with a two-second deadline, bounded input/output and a Linux memory cap. A timeout kills and reaps the process before any channel writes, while retaining normal Python regex syntax and backreferences.
- **Private error diagnostics.** Keep internal exception details in server logs instead of API responses. Preserve public validation codes, missing-TVG-ID guidance and useful stream measurements while removing private URLs and nested diagnostics from successful single-channel responses.
- **All 14 previously open CodeQL findings resolved.** Both Python and JavaScript analyses report zero findings on the tested source; no additional exclusions or alert dismissals were used.
- **Dependency security updates ([#513](https://github.com/krinkuto11/streamflow/pull/513), [#515](https://github.com/krinkuto11/streamflow/pull/515)).** Update affected frontend and Python dependencies, including follow-redirects, Vite/Vitest, certifi and Pillow, with regenerated hash-locked Python requirements. Supported Pillow pixel access preserves media-analysis behavior.

### Changed

- **Frontend and build tooling.** Promote the reviewed React 19, React Router 7, Recharts 3 and related runtime/tooling updates together with current GitHub Actions. Automated dependency updates are credited to [@dependabot](https://github.com/apps/dependabot).
- **Project overview and setup documentation** - Refresh README badges, stable/development image guidance, current features, setup locations and example-data screenshots. Document the required Compose `.env` file and persistent volume; mention Dispatcharr's Unraid store availability without installation instructions.
- **Compatible React updates** - Group React, React DOM and their type packages together for future Dependabot version updates; retain separate review for other major updates.
- **Dependency update policy** - Schedule grouped npm and GitHub Actions minor/patch updates against `dev`, keep major updates separate, and configure security-only updates against `main`. Document review requirements and the coordinated Python lockfile/media-image update process.

### Upgrade notes and credits

- Update the stable image `ghcr.io/krinkuto11/streamflow:latest`, or pin `ghcr.io/krinkuto11/streamflow:2.8.0`. Keep the existing persistent `/app/data` mapping and configured environment/device settings. Take a host-level backup of persistent data before updating.
- Channel-list sorting changes only the current view. Use the separate Channel Order tab to change saved processing order. Concurrent game checks remain opt-in above the default of one.
- Built-in backup/restore was added and removed during development and is not shipped in this release. Existing archive files and user settings are retained.
- Feature suggestions and reports are credited above to their original authors. Implementation, integration and release maintenance are by [@bttfw](https://github.com/bttfw), with the concurrency contribution by [@meekju13391339](https://github.com/meekju13391339) and dependency proposals from Dependabot.
- If a problem appears after updating, open a **new issue** with the version/image tag, reproduction steps and sanitized logs: [report a problem](https://github.com/krinkuto11/streamflow/issues/new/choose).

Full comparison: [2.7.1...2.8.0](https://github.com/krinkuto11/streamflow/compare/2.7.1...2.8.0).
