# Tockett service status

Public status page and a read-only publisher, independent of the quote server.
The app and website read the same `status.json`.

Green means representative checks found no issues, not that every quote is
accurate or continuous. Yellow is disruption or recovery monitoring. Red is
a confirmed fault. Gray is an explicit suspension or discontinuation decision.
An outlined indicator means the current status cannot be confirmed.

The publisher requests the public health endpoint and a consumer-safe observation
export. Health alone never means green. Two failed public health checks, at least
60 seconds apart, confirm a connection fault; recovery requires three distinct
successful observations over at least 15 minutes. Normal maintenance verdicts
retain their original observation times and expire after at most 15 minutes.
Checks are scheduled every five minutes; GitHub scheduling/deployment and caches
can delay publication. The client expires stale observations rather than showing
an old green indicator indefinitely. This is not a real-time availability SLA.

## Publish or correct a decision

Edit `decision.json` on the `main` branch through GitHub or an authorized checkout.
Only the product owner decides suspension/discontinuation; automatic health
checks cannot clear these decisions. Required structure:

```json
{
  "status": "suspended",
  "effectiveAt": "2026-10-21T10:00:00Z",
  "summary": {"zh": "说明暂停范围", "en": "Explain the suspended scope"},
  "detail": {"zh": "已核实原因、恢复时间或未定、后续安排", "en": "Verified reason, recovery date or uncertainty, and next steps"},
  "scope": {"zh": "受影响的市场或功能", "en": "Affected markets or features"}
}
```

Use `discontinued` only for a decision to stop providing the affected service.
To explicitly lift a decision, replace it with `{"status": null}` and commit.
The push publishes a new version; alternatively run the **Publish service status**
workflow manually after correcting a publication failure. Read the public JSON
and page back after deployment. A committed file alone is not a delivered notice.
This publication entry continues to work while the quote host is offline.

`state.json` preserves external checks and up to 30 public status changes.
No phone credentials, server credentials, internal diagnostics or private user
data belong in this repository. No outgoing email or user notification is sent.

## Checks

`python3 -m unittest discover -s tests -v`

The website's local preparation command supplies the approved static pages and
assets. Runtime publication uses only the files in this repository.
