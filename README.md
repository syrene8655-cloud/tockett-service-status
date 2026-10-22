# Tockett service status

## Live architecture — 2026-10-22

Shanghai runs `tockett-status-alert.timer` each minute. It directly requests Hong Kong's
public `/health` and consumer-safe `/status-observation.json`, then applies the original
confirmation/recovery rules in `collect.publish`. Health alone cannot mean green.
The observation's original checkedAt/validUntil are retained, never extended on fetch.
Two failed connections at least 60 seconds apart confirm an external outage; recovery
requires three distinct good observations over at least 15 minutes.

The observer writes private state to `/var/lib/tockett-status-alert/state.json` before
attempting SMTP or public delivery. Grok reads this same direct observation and does not
wait for SMTP. The legacy `public-status` event name remains for authenticated Dots/Grok
deduplication; it now means Shanghai's direct observation, not a fetched webpage verdict.
The HK maintenance/DeepSeek/3-minute and 10-minute HANDOFF and unique-lease rules remain.

Consumer-safe status and history are served directly by Shanghai at:
`https://shanghai-tencent.tail5ba9ea.ts.net/service-status/status.json` and `history.json`.
Only these two JSON files are exposed by a loopback nginx endpoint through the existing
Funnel. Root, diagnostics, state, credentials and directory listings are not exposed.
The existing authenticated event inbox mount remains unchanged.

The website's existing pages and styles remain on GitHub Pages. The browser fetches
live data directly from Shanghai. GitHub is needed to deploy page/code changes, not to
refresh status data or trigger stock alerts. `collect.py` in Actions only mirrors the
Shanghai documents into the old `/status.json` endpoint for installed App compatibility;
it does not check Hong Kong or create a separate verdict. Old App builds keep using
this compatibility copy until their endpoint is updated in a new installed build.
A delayed copy can still expire in those App builds; it cannot trigger a stock alert.

A separate publication check observes both the live Shanghai delivery and the old App copy. After three failures it sends
`[Tockett Status Page]` mail and a separate `status-publication` Grok event. This is not a
stock outage or a direct Codex repair authorization. Recovery also needs three checks.
Stock notices retain `[Tockett Status]` and the existing confirmation/deduplication rules.
An unchanged state sends no repeat notification. SMTP acceptance is not mailbox delivery.

## Owner decisions

The authoritative decision is now `/etc/tockett-status-alert/decision.json` on Shanghai
(root-owned 0600). Edit it atomically on that host using the existing authorized SSH
channel. The format remains a status of null, suspended or discontinued. Suspended and
discontinued require effectiveAt plus bilingual summary/detail/scope (see the historical
format below). Checks cannot clear an owner decision. A malformed decision fails closed;
never substitute null to bypass an unreadable or invalid decision. The repository's
`decision.json` is a migration record, not an active decision input.

## Verification and rollback

Run `python3 -B -m unittest discover -s tests -v` for status rules. Relay tests are in
`../tools/incident-notifications/test_relay.py`. The deployment backs up exact prior
source, unit, state and Funnel configuration privately on Shanghai; rollback must restore
that matching set and only remove the newly added `/service-status` mount. Do not reset
the whole Funnel or alter the original event inbox. See
`../docs/testing/shanghai-direct-status-20261022/RESULT.md` for actual evidence and limits.

---

## Historical setup before the Shanghai direct observer

The following describes the original deployment, superseded by the live architecture
above. Do not re-enable the old website-driven stock alert path from these notes.

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

## Mac backup trigger (2026-10-21)

GitHub's scheduled event did not run during the first hours after this status
repository was created, although push and manual runs succeeded. This Mac has a
secondary LaunchAgent, `work.tockett.status-backup-trigger`, using
`backup_trigger.py` and the plist in `deployment/`. Every five minutes while
the user session and Mac are running, it checks the public JSON. If a
non-decision record has eight minutes or less left, it requests the existing
GitHub workflow. The workflow still makes the external checks and publishes
the status. The trigger never creates a green state or overrides an explicit
suspension/discontinuation decision. It does not store a GitHub token; the
current user's GitHub CLI uses its existing keyring login.

The installed source is in
`~/Library/Application Support/TockettStatusTrigger/trigger.py`, the agent in
`~/Library/LaunchAgents/work.tockett.status-backup-trigger.plist`, and its
event log in `~/Library/Logs/TockettStatusTrigger.log`. A Mac that is asleep,
offline or logged out cannot provide this backup; GitHub's own schedule remains
configured and is not yet proven to run automatically. The status client still
expires old observations after 15 minutes.

## Shanghai status email monitor (2026-10-21)

The existing Shanghai Tencent Cloud server runs
`tockett-status-alert.timer` once per minute. The dedicated script in
`deployment/shanghai_status_monitor.py` reads the same public `status.json`
as the website and app. It uses the Shanghai website monitor's existing
root-owned SMTP configuration and recipient; no credentials are copied into
this repository. The existing Shanghai checks of the Hong Kong `/health` and
authorization boundary continue unchanged.

An explicit red outage or unknown public status sends an immediate email.
Yellow, unreachable, malformed, or expired status requires three consecutive
checks. Three fresh green checks send one recovery email. A suspension or
discontinuation decision sends a state-change email but is not called a
recovery. The last notified state is stored under
`/var/lib/tockett-status-alert/state.json`, and it changes only after SMTP
accepts a message. There are no periodic repeat emails for an unchanged
state. SMTP acceptance alone is not proof of mailbox delivery; the controlled
test email was separately verified in the recipient's Gmail inbox.

The installed script is `/opt/tockett-status-alert/monitor.py`, with service
and timer files under `/etc/systemd/system/`. Check the timer with
`systemctl status tockett-status-alert.timer` and recent results with
`journalctl -u tockett-status-alert.service`. This monitor is independent of
the Mac backup trigger. If the Shanghai host or its email delivery path is
unavailable, it cannot notify the owner; no third observer is configured.
