Upstream: https://github.com/mempool/mempool
Pinned revision: f669f14851fb62b5fdbd16f662667228354b027f

The original blockchain-blocks component sources are supplied here. Wallaby ports its block placement, fill/3D styling and tracking-marker logic into assets/node_blocks.js and assets/mempool_blocks.css, with simplified DOM rendering instead of Angular services. Node observations replace StateService/CacheService and public APIs. Mining pool, advertising, branding, special-block and navigation features are omitted. Wallaby colours and above-block markers replace the upstream colours and below-block marker. No Mempool logos are used.

Copyright (c) 2019-2026 Mempool Holdings S.A. de C.V. and contributors. AGPL-3.0; see LICENSE and the full licence in ../../LICENSE.txt.
