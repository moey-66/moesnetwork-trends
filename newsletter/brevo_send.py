#!/usr/bin/env python3
"""
Create the weekly issue as a Brevo email campaign (a DRAFT by default) using Brevo's API v3.

    python3 brevo_send.py issues/week-2026-11-02                         # create a draft campaign
    NEWSLETTER_SEND_MODE=send python3 brevo_send.py issues/week-...      # create it and send it now
    python3 brevo_send.py issues/week-2026-11-02 --dry-run               # show what would be sent; no network

Settings (environment variables; in GitHub they come from the repo's secrets and variables):
    BREVO_API_KEY         required (secret). Brevo > Settings > SMTP & API > API Keys & MCP.
    BREVO_LIST_ID         the number of the newsletter list. If empty and the account has exactly
                          one list, that list is used; otherwise the run stops and prints your lists.
    BREVO_SENDER_EMAIL    a sender address already added and verified in Brevo (Settings > Senders).
    BREVO_SENDER_NAME     optional, default "Moe's Network".
    NEWSLETTER_SEND_MODE  "draft" (default) or "send".
    BREVO_API_BASE        optional, for testing only (default https://api.brevo.com).

Brevo API docs (developers.brevo.com):
  POST /v3/emailCampaigns                      creates a campaign; it is a draft until sent or scheduled
  POST /v3/emailCampaigns/{campaignId}/sendNow sends it now (402 = not enough sending credits)
  Auth header: "api-key". The HTML must contain Brevo's {{ unsubscribe }} tag (brevo.html has it).

Free plan: 300 emails a day. Over 300 subscribers, Brevo sends to 300 that day and you press
"Requeue" in Brevo on the following days (Brevo help: "What are the limits of the Free plan?").

A marker file <issue folder>/brevo-campaign.json stops the same issue being created twice.
Use --force to create it again anyway.
"""
import json, os, sys, urllib.request, urllib.error

UA = "MoesNetworkNewsletter/1.0 (+https://moesnetwork.pages.dev)"


def api(method, path, key, body=None):
    base = os.environ.get("BREVO_API_BASE", "https://api.brevo.com").rstrip("/")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"api-key": key, "accept": "application/json",
                                          "content-type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8")
        return r.status, (json.loads(raw) if raw.strip() else {})


def explain(e, what):
    msg = e.read().decode("utf-8", "replace")[:800]
    print("Brevo said HTTP %s while %s: %s" % (e.code, what, msg), file=sys.stderr)
    hints = {401: "Check the BREVO_API_KEY secret (Settings > SMTP & API > API Keys & MCP).",
             402: "Not enough sending credits today (free plan: 300 emails a day). The campaign stays in Brevo; send it later.",
             400: "Often an unverified sender (BREVO_SENDER_EMAIL) or a wrong BREVO_LIST_ID."}
    if e.code in hints:
        print(hints[e.code], file=sys.stderr)


def find_list(key):
    lid = os.environ.get("BREVO_LIST_ID", "").strip()
    if lid:
        return int(lid)
    try:
        _, body = api("GET", "/v3/contacts/lists?limit=50&offset=0", key)
    except urllib.error.HTTPError as e:
        explain(e, "reading your lists")
        return None
    lists = body.get("lists") or []
    if len(lists) == 1:
        print("BREVO_LIST_ID not set; using your only list: %s (id %s)" % (lists[0].get("name"), lists[0].get("id")))
        return int(lists[0]["id"])
    print("Set the BREVO_LIST_ID variable to one of these list ids:", file=sys.stderr)
    for l in lists:
        print("  id %s  %s" % (l.get("id"), l.get("name")), file=sys.stderr)
    return None


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry, force = "--dry-run" in sys.argv, "--force" in sys.argv
    if not args:
        print(__doc__)
        return 2
    issue_dir = args[0]
    with open(os.path.join(issue_dir, "issue.json")) as f:
        issue = json.load(f)
    with open(os.path.join(issue_dir, "brevo.html")) as f:
        html = f.read()
    if "{{ unsubscribe }}" not in html:
        print("brevo.html has no {{ unsubscribe }} tag; Brevo requires an unsubscribe link.", file=sys.stderr)
        return 1

    marker = os.path.join(issue_dir, "brevo-campaign.json")
    if os.path.exists(marker) and not force:
        with open(marker) as f:
            print("Already created in Brevo: %s (use --force to create again)" % f.read().strip())
        return 0

    mode = (os.environ.get("NEWSLETTER_SEND_MODE") or "draft").strip().lower()
    if mode not in ("draft", "send"):
        print("NEWSLETTER_SEND_MODE must be 'draft' or 'send', not %r" % mode, file=sys.stderr)
        return 2
    sender_email = os.environ.get("BREVO_SENDER_EMAIL", "").strip()
    sender_name = os.environ.get("BREVO_SENDER_NAME", "").strip() or "Moe's Network"
    payload = {
        "name": "Weekly trend brief " + issue["slug"],      # internal name in Brevo
        "subject": issue["subject"],
        "previewText": issue["preview"],
        "sender": {"name": sender_name, "email": sender_email or "SENDER@EXAMPLE"},
        "htmlContent": html,
        "recipients": {"listIds": [int(os.environ.get("BREVO_LIST_ID") or 0)]},
        "utmCampaign": "weekly trend brief",
    }

    if dry:
        show = dict(payload, htmlContent="<%d characters of HTML>" % len(html))
        print("DRY RUN (nothing sent). Mode: %s\n%s" % (mode, json.dumps(show, indent=1, ensure_ascii=False)))
        return 0

    key = os.environ.get("BREVO_API_KEY", "").strip()
    if not key:
        print("BREVO_API_KEY is not set, so nothing was sent to Brevo. The issue files are still built.")
        return 0
    if not sender_email:
        print("Set the BREVO_SENDER_EMAIL variable to a verified Brevo sender address.", file=sys.stderr)
        return 1
    list_id = find_list(key)
    if list_id is None:
        return 1
    payload["recipients"] = {"listIds": [list_id]}

    try:
        _, body = api("POST", "/v3/emailCampaigns", key, payload)
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")
        if e.code == 400 and "no contacts" in msg.lower():
            # Brevo refuses campaigns for an empty list. Not an error: skip this week, the issue is still archived.
            print("No subscribers on the list yet, so Brevo can't create a campaign. Skipped this week; the issue is saved in newsletter/issues/.")
            return 0
        print("Brevo said HTTP %s while creating the campaign: %s" % (e.code, msg[:800]), file=sys.stderr)
        if e.code == 400: print("Often an unverified sender (BREVO_SENDER_EMAIL) or a wrong BREVO_LIST_ID.", file=sys.stderr)
        if e.code == 401: print("Check the BREVO_API_KEY secret.", file=sys.stderr)
        return 1
    cid = body.get("id")
    record = {"id": cid, "mode": mode, "listId": list_id, "subject": issue["subject"], "sent": False}
    with open(marker, "w") as f:      # written before sending, so a rerun never makes a second campaign
        json.dump(record, f)
    print("Draft campaign created in Brevo (id %s) for list %s." % (cid, list_id))

    if mode == "send":
        try:
            api("POST", "/v3/emailCampaigns/%s/sendNow" % cid, key)
        except urllib.error.HTTPError as e:
            explain(e, "sending the campaign")
            print("The campaign is still in Brevo as a draft. Send it from your campaigns list.", file=sys.stderr)
            return 1
        record["sent"] = True
        with open(marker, "w") as f:
            json.dump(record, f)
        print("Sent now (campaign id %s)." % cid)
    else:
        print("Open your email campaigns in Brevo, check the draft, send yourself a test, then send it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
