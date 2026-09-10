# Compatibility strategy

The project avoids pinning production to one upstream commit. CI checks the minimum contracts it depends on.

Every pull request runs the unit/contract suite and a live source compatibility probe against current upstream default branches. A scheduled workflow repeats the upstream probe every six hours.

Checks validate:

- current Mirza Agent functions can still be patched safely in memory
- required x-ui-reseller-panel `/api/v1` routes still exist
- core 3x-ui client attach/detach/reset primitives still exist

A scheduled compatibility failure is a review signal, **not permission to modify production automatically**. The Mirza server watcher likewise uses only the already-installed patcher and fails closed if source anchors stop matching.
