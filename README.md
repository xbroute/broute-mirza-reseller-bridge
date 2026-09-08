# Broute Mirza Reseller Bridge

Integration and compatibility layer between MirzaBot, x-ui-reseller-panel, and 3x-ui.

This repository is used for development, testing, deployment, compatibility checks, secure installation, updates, and rollback tooling for the Broute infrastructure.

## Architecture

MirzaBot → Broute Bridge → x-ui-reseller-panel → 3x-ui Central → Nodes

## Status

Initial repository. Implementation in progress.

## Safety

Production deployment must support:

* pre-change backups
* configuration snapshots
* transactional installation
* automatic health checks
* automatic rollback on failed deployment
* manual rollback to previous releases
* non-destructive Mirza compatibility patching
* encrypted credential storage
