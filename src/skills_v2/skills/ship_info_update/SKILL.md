# Ship Information Update V2

Only use the transaction tools `prepare_ship_update`, `commit_ship_update`, and
`cancel_ship_update`. Never expose or invoke low-level write APIs. Prepare a
normalized draft, show the user its fields and validation errors, and commit only
after an explicit confirmation in the same session. Say “更新成功” only when the
write adapter returns the exact `success` state; accepted, pending, dry-run, unknown,
and failed states must be described conservatively.
