#!/usr/bin/env python3
"""
Approval step for the weekly newsletter: nothing reaches subscribers until Moey says "approve".

    python3 review.py start <issue_dir> [--practice]   # Sunday: open the approval issue + email the preview
    python3 review.py remind                           # hourly: reminder emails, expiry
    python3 review.py decide <approve|cancel> <github issue number>   # from a comment by the owner

Timeline (South African time, SAST = UTC+2), for the Mon-Fri week starting W:
    Sunday W+6 18:00   draft created in Brevo, approval issue opened, preview emailed
    from 19:00         a reminder every hour until approve/cancel
    22:00 - 06:00      quiet hours (no reminders)
    Monday W+7 07:00   send time. Approve before it = scheduled for 07:00; after it = sent at once.
    Monday W+7 18:00   cut-off: still not approved = skipped for the week, reminders stop.
Practice runs use the same rules counted from when they started, never touch Brevo campaigns and
never publish the website archive.

State lives in <issue_dir>/approval.json and is committed by the workflow.

Environment: BREVO_API_KEY, BREVO_SENDER_EMAIL, BREVO_SENDER_NAME, NEWSLETTER_REVIEWER_EMAIL
(default: the sender address), GITHUB_TOKEN, GITHUB_REPOSITORY, NEWSLETTER_OWNER (GitHub login
allowed to decide; default the repo owner).
"""
import glob, json, os, shutil, sys, urllib.error, urllib.request
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SAST = timezone(timedelta(hours=2))
UA = "MoesNetworkNewsletter/1.0 (+https://moesnetwork.pages.dev)"
QUIET_START, QUIET_END = 22, 6          # SAST hours
REMIND_GAP = timedelta(minutes=50)      # cron runs hourly; a little slack for GitHub's late starts
LABEL = "newsletter-approval"


def now():
    override = os.environ.get("REVIEW_NOW")          # for testing: 2026-11-15T19:05:00+02:00
    return datetime.fromisoformat(override) if override else datetime.now(timezone.utc)


def http(method, url, headers, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=dict(headers, **{
        "accept": "application/json", "content-type": "application/json", "User-Agent": UA}))
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}


def brevo(method, path, body=None):
    key = os.environ["BREVO_API_KEY"].strip()
    return http(method, "https://api.brevo.com" + path, {"api-key": key}, body)


def github(method, path, body=None):
    tok = os.environ["GITHUB_TOKEN"].strip()
    return http(method, "https://api.github.com/repos/%s%s" % (os.environ["GITHUB_REPOSITORY"], path),
                {"authorization": "Bearer " + tok, "x-github-api-version": "2022-11-28"}, body)


def reviewer():
    return (os.environ.get("NEWSLETTER_REVIEWER_EMAIL") or os.environ.get("BREVO_SENDER_EMAIL") or "").strip()


def send_mail(subject, html):
    to = reviewer()
    if not to:
        print("No reviewer email (NEWSLETTER_REVIEWER_EMAIL / BREVO_SENDER_EMAIL); email skipped.", file=sys.stderr)
        return
    sender = {"name": os.environ.get("BREVO_SENDER_NAME") or "Moe's Network", "email": os.environ["BREVO_SENDER_EMAIL"]}
    brevo("POST", "/v3/smtp/email", {"sender": sender, "to": [{"email": to}], "subject": subject,
                                      "htmlContent": html, "tags": ["newsletter-review"]})
    print("Emailed %s: %s" % (to, subject))


# ---- timeline -----------------------------------------------------------------------------
def timeline(state):
    """Return (first_reminder, send_at, cutoff) as aware datetimes."""
    if state.get("practice"):
        start = datetime.fromisoformat(state["createdAt"])
        return start + timedelta(hours=1), start + timedelta(hours=13), start + timedelta(hours=24)
    w = date.fromisoformat(state["weekStart"])
    sun, mon = w + timedelta(days=6), w + timedelta(days=7)
    at = lambda d, h: datetime(d.year, d.month, d.day, h, 0, tzinfo=SAST)
    return at(sun, 19), at(mon, 7), at(mon, 18)


def quiet(t):
    h = t.astimezone(SAST).hour
    return h >= QUIET_START or h < QUIET_END


def fmt(t):
    return t.astimezone(SAST).strftime("%a %d %b %H:%M")


# ---- emails -------------------------------------------------------------------------------
def banner(state, kind):
    first, send_at, cutoff = timeline(state)
    url = state.get("issueUrl", "")
    head = {"first": "Your newsletter is ready for approval",
            "reminder": "Reminder %d: this week's newsletter still needs your approval" % state.get("reminders", 0),
            }[kind]
    practice = ('<p style="margin:0 0 10px;color:#b45309;font-weight:700">PRACTICE RUN: nothing will be sent to subscribers.</p>'
                if state.get("practice") else "")
    return (
        '<div style="background:#111;border:2px solid #d4af37;border-radius:10px;padding:18px 20px;margin:16px auto;'
        'max-width:600px;font-family:Arial,Helvetica,sans-serif;color:#fff">'
        + practice +
        '<p style="margin:0 0 8px;font-size:18px;font-weight:700;color:#d4af37">%s</p>'
        '<p style="margin:0 0 12px;font-size:14px;line-height:1.5">Nothing goes to subscribers until you approve. '
        'Approve before <b>%s</b> and it goes out then; later than that and it goes out straight away. '
        'Not approved by <b>%s</b> = skipped this week.</p>'
        '<p style="margin:0 0 14px"><a href="%s" style="display:inline-block;background:#d4af37;color:#111;'
        'text-decoration:none;font-weight:700;padding:12px 18px;border-radius:8px">Open the approval page</a></p>'
        '<p style="margin:0;font-size:13px;line-height:1.5;color:#ddd">On that page (GitHub app or website), add a comment '
        'with just the word <b>approve</b> or <b>cancel</b>. The email below is exactly what subscribers will get.</p>'
        '</div>' % (head, fmt(send_at), fmt(cutoff), url))


def preview_email(state, kind):
    with open(os.path.join(state["_dir"], "email.html")) as f:
        html = f.read()
    b = banner(state, kind)
    i = html.lower().find("<body")
    if i >= 0:
        j = html.find(">", i) + 1
        return html[:j] + b + html[j:]
    return b + html


def note_email(title, lines):
    return ('<div style="font-family:Arial,Helvetica,sans-serif;max-width:600px;margin:16px auto;padding:18px 20px;'
            'border:2px solid #d4af37;border-radius:10px"><p style="font-size:18px;font-weight:700;margin:0 0 10px">%s</p>%s</div>'
            % (title, "".join('<p style="margin:0 0 8px;line-height:1.5">%s</p>' % l for l in lines)))


# ---- state --------------------------------------------------------------------------------
def load(path):
    with open(path) as f:
        s = json.load(f)
    s["_dir"] = os.path.dirname(path)
    return s


def save(state):
    d = state["_dir"]
    with open(os.path.join(d, "approval.json"), "w") as f:
        json.dump({k: v for k, v in state.items() if k != "_dir"}, f, indent=1)


def all_states():
    paths = glob.glob(os.path.join(HERE, "issues", "*", "approval.json")) + \
            glob.glob(os.path.join(HERE, "practice", "*", "approval.json"))
    return [load(p) for p in sorted(paths)]


def close_issue(state, comment):
    if not state.get("issueNumber"):
        return
    try:
        github("POST", "/issues/%s/comments" % state["issueNumber"], {"body": comment})
        github("PATCH", "/issues/%s" % state["issueNumber"], {"state": "closed"})
    except urllib.error.HTTPError as e:
        print("Could not update the GitHub issue: HTTP %s" % e.code, file=sys.stderr)


# ---- commands -----------------------------------------------------------------------------
def cmd_start(issue_dir, practice):
    issue_dir = os.path.abspath(issue_dir)
    with open(os.path.join(issue_dir, "issue.json")) as f:
        issue = json.load(f)
    state_path = os.path.join(issue_dir, "approval.json")
    if os.path.exists(state_path) and not practice:
        print("Approval already started for %s; nothing to do." % issue["slug"])
        return 0
    campaign = None
    if not practice:
        marker = os.path.join(issue_dir, "brevo-campaign.json")
        if not os.path.exists(marker):
            print("No Brevo draft for this issue (no subscribers yet?), so there is nothing to approve.")
            return 0
        with open(marker) as f:
            campaign = json.load(f).get("id")
    state = {"_dir": issue_dir, "slug": issue["slug"], "subject": issue["subject"], "weekStart": issue["weekStart"],
             "weekLabel": issue.get("weekLabel", ""), "campaignId": campaign, "practice": practice,
             "status": "pending", "createdAt": now().isoformat(), "reminders": 0, "lastEmailAt": None}
    first, send_at, cutoff = timeline(state)
    body = ("**%s**\n\n%sThe preview has been emailed to the reviewer. Reply to this issue with a comment that is just:\n\n"
            "- `approve`: goes out %s (or at once if that time has passed)\n- `cancel`: not sent this week\n\n"
            "Reminders every hour from %s (quiet 22:00-06:00). Not approved by %s = skipped.\n\n"
            "Only comments from @%s count." % (issue["subject"], "**PRACTICE RUN: nothing is sent to subscribers.**\n\n" if practice else "",
                                              fmt(send_at), fmt(first), fmt(cutoff), owner()))
    try:
        try:
            github("POST", "/labels", {"name": LABEL, "color": "d4af37", "description": "Weekly newsletter waiting for approval"})
        except urllib.error.HTTPError:
            pass                                            # label already exists
        gi = github("POST", "/issues", {"title": "%sApprove newsletter: %s" % ("[Practice] " if practice else "", issue.get("weekLabel") or issue["slug"]),
                                       "body": body, "labels": [LABEL]})
        state["issueNumber"], state["issueUrl"] = gi["number"], gi["html_url"]
    except urllib.error.HTTPError as e:
        print("Could not open the approval issue: HTTP %s %s" % (e.code, e.read()[:300]), file=sys.stderr)
        state["issueUrl"] = "https://github.com/%s/issues" % os.environ.get("GITHUB_REPOSITORY", "")
    save(state)
    send_mail("%s[Approve?] %s" % ("[Practice] " if practice else "", issue["subject"]), preview_email(state, "first"))
    state["lastEmailAt"] = now().isoformat()
    save(state)
    return 0


def cmd_remind():
    t = now()
    for s in all_states():
        if s["status"] != "pending":
            continue
        first, send_at, cutoff = timeline(s)
        if t >= cutoff:
            s["status"] = "expired"
            s["decidedAt"] = t.isoformat()
            save(s)
            close_issue(s, "Not approved by %s, so this week's newsletter was skipped. Nothing was sent." % fmt(cutoff))
            send_mail("%sSkipped: %s" % ("[Practice] " if s.get("practice") else "", s["subject"]),
                      note_email("This week's newsletter was skipped", [
                          "It wasn't approved by %s, so nothing was sent to subscribers." % fmt(cutoff),
                          "The draft is still in Brevo if you want to send it by hand."]))
            continue
        if t < first or quiet(t):
            continue
        last = datetime.fromisoformat(s["lastEmailAt"]) if s.get("lastEmailAt") else None
        if last and t - last < REMIND_GAP:
            continue
        s["reminders"] = s.get("reminders", 0) + 1
        send_mail("%s[Reminder %d · approve?] %s" % ("[Practice] " if s.get("practice") else "", s["reminders"], s["subject"]),
                  preview_email(s, "reminder"))
        s["lastEmailAt"] = t.isoformat()
        save(s)
    return 0


def owner():
    return (os.environ.get("NEWSLETTER_OWNER") or os.environ.get("GITHUB_REPOSITORY", "/").split("/")[0]).strip()


def publish_archive(s):
    src = os.path.join(s["_dir"], "archive")
    dst = os.path.join(HERE, "archive")
    if not os.path.isdir(src):
        print("No pending archive pages found; website archive not updated.", file=sys.stderr)
        return
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(src):
        shutil.copy2(os.path.join(src, name), os.path.join(dst, name))
    print("Website archive updated with %s." % s["slug"])


def cmd_decide(word, issue_number):
    word = word.strip().lower()
    match = [s for s in all_states() if str(s.get("issueNumber")) == str(issue_number)]
    if not match:
        print("No newsletter waiting on issue #%s." % issue_number)
        return 0
    s = match[0]
    if s["status"] != "pending":
        github("POST", "/issues/%s/comments" % issue_number, {"body": "Already %s; nothing changed." % s["status"]})
        return 0
    t = now()
    first, send_at, cutoff = timeline(s)
    if word == "cancel":
        s.update(status="cancelled", decidedAt=t.isoformat())
        save(s)
        close_issue(s, "Cancelled. Nothing will be sent this week.")
        send_mail("Cancelled: %s" % s["subject"], note_email("Newsletter cancelled", ["Nothing will be sent to subscribers this week."]))
        return 0
    if word != "approve":
        return 0
    if s.get("practice"):
        s.update(status="approved", decidedAt=t.isoformat())
        save(s)
        close_issue(s, "Practice approval received. In a real week the newsletter would now go out %s." % fmt(max(send_at, t)))
        send_mail("[Practice] Approved: %s" % s["subject"], note_email("Practice approval worked", [
            "In a real week this is the moment the newsletter gets booked to go out %s." % fmt(send_at),
            "Nothing was sent to subscribers."]))
        return 0
    cid = s["campaignId"]
    try:
        if t < send_at - timedelta(minutes=5):
            brevo("PUT", "/v3/emailCampaigns/%s" % cid, {"scheduledAt": send_at.isoformat()})
            when = fmt(send_at)
        else:
            brevo("POST", "/v3/emailCampaigns/%s/sendNow" % cid)
            when = "now"
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")[:400]
        github("POST", "/issues/%s/comments" % issue_number,
               {"body": "Brevo refused (HTTP %s): %s\n\nNothing was sent. The draft is still in Brevo." % (e.code, msg)})
        send_mail("Problem sending: %s" % s["subject"], note_email("Brevo refused the send", [
            "HTTP %s: %s" % (e.code, msg), "Nothing was sent. Open Brevo > Campaigns to send the draft by hand."]))
        return 1
    s.update(status="approved", decidedAt=t.isoformat(), sendAt=when)
    save(s)
    publish_archive(s)
    close_issue(s, "Approved. Going out %s." % when)
    send_mail("Approved: %s" % s["subject"], note_email("Approved", [
        "Your newsletter goes out to subscribers %s." % ("at " + when if when != "now" else "right now"),
        "It is also on the website archive."]))
    return 0


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__); return 2
    if a[0] == "start" and len(a) >= 2:
        return cmd_start(a[1], "--practice" in a)
    if a[0] == "remind":
        return cmd_remind()
    if a[0] == "decide" and len(a) >= 3:
        return cmd_decide(a[1], a[2])
    print(__doc__); return 2


if __name__ == "__main__":
    sys.exit(main())
