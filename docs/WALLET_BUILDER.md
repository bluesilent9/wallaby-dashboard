# Wallet Builder — Dashboard V1 Build 12 / Agent V2 Build 10

Wallet Builder is available after login, independently of Ownership. It proposes mainnet public policies. Approval, key generation, encryption, disc writing and signing happen locally on the chosen Agents. The Dashboard receives public keys, descriptors, signed acknowledgements and PSBTs; no private wallet package or passphrase is accepted.

1. Choose Single Signature, Multi-Signature, Vault or Multi-Signature Vault. Give the wallet a name; choose M of N for multisig and a mandatory block height for vaults. N is at most 15.
2. Discover connected, synchronized Agents. Compare every full SHA256 identity fingerprint with the corresponding Agent's local Services page. A self-signed identity delivered through Dashboard is not independent authentication.
3. Name and assign every role to one producing Agent. Choose encrypted node storage or physical disc. Several roles on one Agent still use distinct keys, but concentrate custody on that computer. Each disc role needs its own verified disc and local encryption credentials.
4. Request approval. On every producer, review participation and generate keys using the Agent's native local dialogs. Then compare the exact policy hash, roles and six receive/change sample addresses (indices 0, 1, 10). Approve that policy locally. Finish required disc writes and readback checks on the Agents.
5. Dashboard verifies every signed contribution, policy approval and disc-backup proof, then relays the full authenticated completion evidence. Agents independently verify it before marking their monitors ready. Reload Node Wallets in Ownership and assign its owner before using the new wallet. Confirm the local public monitor is marked ready before funding it.

The restricted policy constructions match Agent Build 9 exactly:

- Single: `wpkh(KEY)`
- Multisig: `wsh(sortedmulti(M,KEYS...))`
- Vault: `wsh(and_v(v:after(HEIGHT),pk(KEY)))`
- Multisig vault: `wsh(and_v(v:after(HEIGHT),multi(M,KEYS...)))`

Roles are ordered by stable role ID; multisig-vault keys use that deterministic order. There is no bypass branch. Descriptors include checksums. Contributions must use independent public accounts and matching receive/change branches.

## Interrupted setup

Sessions expire after at most 24 hours. A Dashboard restart interrupts unfinished ceremonies; an Agent reconnection during one also stops coordination. Stop/reconcile pending setups locally before starting a fresh ceremony. Stopping Dashboard coordination does not delete Core wallets or erase local pending keys. Already authenticated public evidence is retained in profile backups, and archive import revalidates it.

## Signing, including physical cosigners

Transaction Builder constructs one unchanged transaction for all signers. Its cards list healthy Agents that actually hold a matching private signing account. Public watch monitors cannot sign. Returned partial signatures are verified against the original previous outputs and exact transaction; repeated signatures by one key count once. Each input must reach its own threshold before broadcast becomes available. Vault inputs carry their verified witness policy, key origins and mandatory height lock; immature inputs are excluded.

For a disc cosigner, use the Agent's **Disc Operations** page to load its encrypted package into protected RAM. Several different discs can remain loaded together. Select **Refresh signing accounts** in Transaction Builder, choose the required local account, then approve on the Agent's **Authorise** page. No private account is restored into the persistent Core by this flow. Dashboard recognizes public routing aliases by matching the returned public policies; alias names do not establish ownership. Temporary aliases are signing locations, never blockchain-history replicas or synchronization sources. Unloading the archive or restarting its protected workspace removes that signing location. Never upload the backup, decryption password or wallet password to Dashboard.

Cosigner accounts matching a shared monitor are excluded from automatic missing-wallet transfers. Empty signer accounts are excluded from balance/Ownership duplication; an account with independent transaction history remains visible. Full shared public policies on several nodes remain replicas of one wallet, irrespective of wallet names or private/watch status.


Build 16 shows discovery progress next to its button. Repeated checks preserve an outstanding identity challenge; completed discovery can be refreshed with a new challenge. Stale signed public replies cannot satisfy that new challenge. No signature verification or local fingerprint comparison is bypassed.

## Build 17 role names

Role names appear only for Multi-Signature and Multi-Signature Vault. Single Signature and Vault automatically use one signing role; select its producing Agent and key storage normally. This is a Dashboard presentation change and requires no Agent update.
