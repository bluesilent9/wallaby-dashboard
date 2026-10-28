# Wallaby Agent protocol — v1 with RPC capability v1 (V156)

Dashboard is the hub. Wallaby Agent initiates every connection through its authorised SSH tunnel, polls for commands, calls its local Bitcoin Core, and posts results. No server-initiated connection to a laptop is required. The production Agent application is **not** bundled with this Dashboard; the server contract is implemented and integration-tested with the actual Agent client and a simulated Core HTTP endpoint.

## Transport

- Dashboard browser: **38951**, unchanged.
- Agent HTTP/JSON: **56443**, bound to IPv4 `127.0.0.1` and IPv6 `::1` by default. `--agent-port` changes it; `--ipv4-only` / `--ipv6-only` apply to both listeners. Client-side SSH forwarding ports may differ.
- These are separate loopback listeners. SSH authenticates and restricts access; the browser and agent endpoints are not public Internet services. Agents do not use browser session credentials. Core RPC credentials remain on the node computer and must never be included in a hub message.
- POST headers: `Content-Type: application/json`, one Content-Length, `X-Wallaby-Agent: 1`. After registration add `Authorization: Bearer <connection_token>`. Tokens are temporary, not entered by users, never URL parameters, logs or profile data.
- Control requests: at most 4,096 bytes. `/agent/v1/result`: at most 16 MiB plus 4 KiB of envelope space; the decoded result/error has its own 16 MiB limit. Oversized results must be narrowed/paginated using Core's method parameters. No chunks are silently discarded.
- JSON must be UTF-8, have no duplicate keys, and no NaN/infinity. Browser Origin headers are rejected. Host must name a loopback endpoint, with any valid tunnel port. Unknown endpoints/fields are rejected.

## 1. Registration and permission negotiation

`POST /agent/v1/register`

```json
{"mac":"02:11:22:33:44:55","hostname":"example-node","rpc":{"version":1,"methods":["getblockchaininfo","getbalances","listdescriptors"]}}
```

The example identifies no built-in device. The Agent chooses a stable unicast adapter MAC consistently across restarts. Hostname/MAC are normalised and are self-reported identity hints, not proof of device authenticity. SSH supplies authentication. Changed MACs create new remembered nodes; a hostname can update for the same MAC when the previous connection is no longer live.

`rpc` is optional for backward compatibility with V154 presence-only agents. Methods are negotiated as the intersection of the agent's enabled methods and the hub's permitted methods. Advertising a method never overrides the hub's policy. The Agent must independently enforce its advertised methods and the public-data restrictions below.

Response:

```json
{"protocol":"wallaby-agent","version":1,"connection_token":"<temporary secret>","node_id":"<stable ID>","heartbeat_seconds":15,"disconnect_after_seconds":45,"capabilities":["presence","rpc"],"rpc_methods":["getbalances","getblockchaininfo","listdescriptors"],"commands":[]}
```

Only advertise methods that the local Agent is configured and capable of executing. Core GUI requires RPC server mode enabled. Use the relevant Core version's method documentation; the initial target is Core V31.

`GET /agent/v1/health` exposes the server protocol/capabilities and permitted method names, without node identities or credentials.

## 2. Heartbeat, command poll and disconnection

Every 15 seconds send `POST /agent/v1/heartbeat` with `{}`. Its response includes any pending command. A dedicated command loop should also send `POST /agent/v1/poll`:

```json
{"wait_seconds":20}
```

The integer wait is from 0 to 20 seconds. The poll returns immediately when work is available, otherwise at the timeout. A poll refreshes in-memory connectivity; regular heartbeats persist the Last seen timestamp. Keep the heartbeat/poll loop active while a separate worker executes a slow Core RPC. Otherwise an RPC that takes longer than 45 seconds could look like a disconnected Agent.

Both responses use `{"ok":true,"commands":[...]}` and deliver at most one RPC command per response. A command has:

```json
{"id":"<request ID>","kind":"rpc","method":"getbalances","params":[],"wallet":"Exact Core wallet name","expires_in_seconds":29.5}
```

The request ID identifies one immutable request. `wallet` is null for node-wide methods. `params` is an array or named-parameter object. Expiry is a remaining duration, independent of the Agent's wall clock. Never start expired work.

Gracefully end the connection with `POST /agent/v1/disconnect` and `{}`. Heartbeat/poll authentication expiry returns HTTP 401: register again. Do not register on each poll. Two live registrations claiming the same hostname or MAC produce conflict warnings and disable RPC to the affected connections. Resolve the duplicate before retrying any operation.

## 3. Acknowledge before execution

`POST /agent/v1/ack`

```json
{"id":"<request ID>"}
```

Response: `{"ok":true,"state":"running","execute":true}` for the first acknowledged delivery. Duplicate acknowledgements return `execute:false`. Terminal/expired requests cannot be restarted. Maintain a local per-connection record of request IDs and completed results: duplicate deliveries are not new work.

Execute only after receiving `execute:true`. If that acknowledgement response is lost, do not blindly execute or assume another acknowledgement authorises execution. Report/resolve the uncertain state; the request will time out. This favours avoiding an accidental repeated wallet operation over pretending that distributed systems can guarantee exactly-once execution.

## 4. Local Core call and result

The Agent sends Core a JSON-RPC 2.0 request using the command ID, method and params. For wallet methods, URL-encode the exact wallet name as one path segment in `/wallet/<encoded-name>/`. The Core host, port and credentials come from trusted local Agent configuration, never from hub parameters. Verify the returned Core response ID. Do not invoke a shell or construct console command strings.

Post exactly one of:

```json
{"id":"<request ID>","result":{"blocks":970000,"chain":"main"}}
```

```json
{"id":"<request ID>","error":{"code":-18,"message":"Requested wallet does not exist."}}
```

`POST /agent/v1/result` accepts arbitrary bounded JSON results, including null. Errors require code (integer or string) and message; optional data is supported. The server correlates replies to both request ID and the assigned connection token. Another node cannot complete a request. Duplicate or late replies cannot overwrite a terminal result; the acknowledgement includes `accepted:false`.

Preserve decimal values from Core without binary-floating-point conversion. Forward the original JSON value or decode numbers with an exact decimal parser. The hub exposes fractional JSON numbers as **plain decimal strings** to preserve monetary precision (`0.00000001` becomes `"0.00000001"`). Integer heights/counts remain integers. Do not convert BTC to floats in the future Agent.

## 5. Dashboard application API

Authenticated, logged-in browser workspaces can queue:

`POST /api/nodes/rpc`

```json
{"node_id":"<stable node ID>","method":"getblockchaininfo","params":[],"timeout_seconds":30}
```

Wallet calls additionally need `wallet`. Timeout defaults to 30 seconds and must be from 1 to 3,600 seconds. Parameters are limited to 4 MiB. The response is HTTP 202 with a request ID/state. Poll `GET /api/nodes/rpc/result?id=<request ID>` in that same browser/profile. Results are not available to another browser workspace or a different selected profile. Request parameters, response bodies and credentials are not written into session logs; method/ID/state summaries are.

The Nodes page's **Check node** button uses this exact API for `getblockchaininfo`. It is disabled for disconnected agents, conflicting identities, or agents not advertising that method.

Future Python features can use `sessions.nodes.rpc.queue(...)`, `.status(...)`, or synchronous `.call(...)`. The same policy, deadlines and identity checks apply. No generic node URL or authentication secret is accepted through these interfaces.

## Permissions and lifecycle

The method catalogue in `agent_rpc.py` covers node/chain/mempool reads, descriptor/address inspection, wallet balances/history, historical scanning/block retrieval, and public watch-only setup. Wallet methods require an explicit wallet endpoint. `listdescriptors` permits only its public form. `createwallet` permits only named, blank, private-key-disabled descriptor-wallet creation. `importdescriptors` validates supported public descriptors before delivery.

Signing, wallet unlocking, private exports, broadcast, arbitrary method names and arbitrary commands are not allowed. Cold-storage signing remains a local user-controlled action. An Agent must apply these same rules locally even if its SSH-trusted hub is compromised. Expanding the catalogue is a deliberate future permission change, not an agent-supplied bypass.

States are queued → delivered → running → succeeded/failed, with timed_out also terminal. Pending requests fail on connection expiry/disconnect, identity conflict, profile replacement or server shutdown. They are **not automatically replayed** on reconnect. A failure/timeout after delivery does not prove Core cancelled the operation: the local operation may still finish. Reconcile the actual Core state before deliberately issuing a new write request.

The queue is bounded (16 pending requests per node, 256 retained requests and 64 MiB aggregate request/result storage). Terminal results expire after 10 minutes. Live requests/results are memory-only; they do not survive service restart or enter profile backups. Remembered node identities still reside in `data/nodes.json` and profile.7z.

## V156 node wallet discovery

Load Node Wallets queries every connected RPC-ready node. Required read methods: listwallets, listwalletdir, getwalletinfo, listdescriptors (private=false), listsinceblock (include_change=true), getaddressinfo. Wallet endpoints retain the exact node-local name, including the empty default name. Replicas are recognised by public descriptors rather than labels. An unloaded wallet is reported for the user to load in Core; Dashboard does not silently load or unlock it. A failed node prevents publishing a partial catalog. Public tracing remains separate.

Application-internal read workflows consume completed RPC payloads promptly. Short-lived bounded tombstones retain only token/state metadata so a retried Agent acknowledgement/result is safely recognised without retaining wallet-history payloads. Cancelling Load Node Wallets cancels its pending read waits; no write RPC is replayed.

## Exact Core wallet routing and dispatch capacity

Wallet-scoped requests use `/wallet/` plus the URL-encoded exact Core wallet name, without appending a slash. A null wallet is node-wide; an empty string denotes Core's default wallet. Names are never inferred from display labels.

Dashboard V1 Build 6 limits delivered/running work to two commands per Agent connection, with queued work released as results arrive. Agent V2 Development Build 4 independently bounds execution to two workers and tolerates a brief completion/slot-release race. Multiple nodes can execute concurrently, and equivalent public-descriptor replicas can share follow-up reads. These limits do not insert an artificial delay between successful requests.

## Build 6 background scheduling

Read-only calculation workers use a private loopback bridge with a per-workspace bearer secret and a strict method allowlist. Core cookies and private keys never enter worker configuration. Foreground calls use priority 0; fee observations use priority 100. Queued foreground work precedes background work, and background calls wait while foreground commands are queued or executing. Already executing RPCs are not preempted. No Agent wire-protocol change or signing permission is required.
