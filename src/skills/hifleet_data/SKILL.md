# HiFleet Data V2

This is a locked, read-only adapter for verified HiFleet data capabilities. State
only facts supported by returned data. Do not expose account, billing, registration,
purchase, contact-unlock, console, or any other upstream write capability. Include
the tool result's version metadata in trace data; a successful request alone does
not establish that a customer-facing conclusion is semantically correct.
