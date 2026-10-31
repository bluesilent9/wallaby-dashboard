# V1 Development Build 19 validation

206 Python regression tests passed, including current Agent Build 18 contract validation. Sixteen JavaScript programs and all asset syntax checks passed. New cases cover retirement readiness and cancellation, owner/zero-balance controls, sidebar-pane scrolling, and the restored estimator. Timezone checks cover Darwin +09:30, Sydney DST and UTC-offset ordering; confidence tests verify that the chosen count meets 99% and the next candidate does not. Map/list interaction, read-only calculated height, comma-free clipboard output and date/timezone invalidation are tested.

Browser preview uses explicitly simulated data, no real wallets or nodes. It verified single-wallet naming, the restored vault calendar/map and calculated local/UTC times, disabled funded-wallet hover action, equal card layout, the per-Agent extinction status table, and actual navigation-pane scrolling. Preview screenshots are development review artifacts, not packaged user data.

Pending-block rendering passes simulated mempool/package tests. The user's real Agent getrawmempool failure is not resolved or claimed fixed; see docs/AGENT_FOLLOW_UP.md. It returned a generic local RPC failure before Dashboard received any mempool data. No public fallback or guessed blocks are used.

Agent Build 18 supports the matching retirement/archive/delete operation. Delivered cancellation still requires local Agent denial or a later Agent implementing the documented cancellation capability. Tests do not delete production wallets or write/destroy discs. No production service, node, pruning setting or installed configuration was changed.
