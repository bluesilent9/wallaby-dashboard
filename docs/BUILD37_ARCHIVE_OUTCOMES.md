# Archive outcome handoff — Dashboard Build 37

Use the existing wallet_archive_queue_v1 proposal and archive_queue_result envelope. Results are signed with the paired Agent identity. Dashboard verifies the exact payload, queue ID, request ID and originating node.

Allowed result statuses: queued, error, deleted, complete. Archive disabling is removed. queued/error leave a task active. deleted is terminal and removes it from future queue proposals; it means the local request was deleted, not that a wallet or disc was deleted. complete remains terminal and requires verified disc readback and an image SHA-256 for archive_wallet; destroy_disc retains its local confirmation and mixed-disc replacement checks.

Identical terminal receipt retries are accepted without changing timestamps. A different terminal result is rejected. Local confirmed outcomes arriving after Dashboard withdrawal are retained. A fresh explicit request for the same wallet may have a new request identity.

Notifications use the Dashboard receipt time in UTC, Agent hostname and wallet/task name. Deleted is red; complete is green. Notices appear in Notifications and Wallets, are read when either page is viewed and unread elsewhere. Read/dismiss state is per profile. Dismissal in Notifications persists across reconnects/restarts. Old stored terminal outcomes without a receipt timestamp do not generate upgrade notices. Terminal queue records are retained for replacement and rename evidence, while the queue view lists only outstanding requests.

Archive notices update their own Wallets region to preserve filters, focus and scroll. Dashboard does not acknowledge physical disc destruction on behalf of an Agent.
