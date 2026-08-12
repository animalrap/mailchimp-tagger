"""
banana_tagger.py

Tag Mailchimp audience members from a CSV that has names only, no
email addresses, by matching those names against your existing
Mailchimp audience.

Useful any time you have a name list from somewhere that isn't already
tied to Mailchimp (an event roster, a waitlist, a check-in export, a
partner's spreadsheet) and want to tag those contacts for a segmented
send without typing each name into search by hand. If your source list
already has emails, use Mailchimp's native CSV import instead, this
tool is for when all you have is a name.

SETUP (one time):
  1. pip install -r requirements.txt
  2. Get your Mailchimp API key: Account > Extras > API keys
     The data center prefix is the letters/numbers after the "-" in the
     key (e.g. key "abc123-us21" means MAILCHIMP_SERVER = "us21")
  3. Get your Audience/List ID: Audience > Settings > Audience name and
     defaults
  4. Set these as environment variables (do NOT hardcode them in this
     file or commit them to git):
       MAILCHIMP_API_KEY
       MAILCHIMP_SERVER
       MAILCHIMP_LIST_ID
     See .env.example for a local-dev pattern.

USAGE:
  1. Put names in a CSV with a single column header exactly named "name"
     (lowercase), one full name per row, formatted "First Last" the same
     way it appears in Mailchimp. See sample_names.csv.
  2. python banana_tagger.py players.csv "Fall Classic 2026"

  Add --dry-run to see matched/ambiguous/unmatched counts without
  creating the tag or writing anything to Mailchimp:
  3. python banana_tagger.py players.csv "Fall Classic 2026" --dry-run

The script will not guess on ambiguous matches (e.g. two "Mike Smith"s
in your audience) -- it lists those separately so you can resolve them
by hand rather than risk tagging the wrong contact.
"""

import argparse
import csv
import os
import sys

import requests


def _get_config():
    """Read Mailchimp config from environment variables at call time,
    not at import time, so a GUI wrapper can validate before any
    request fires."""
    api_key = os.environ.get("MAILCHIMP_API_KEY")
    server = os.environ.get("MAILCHIMP_SERVER")
    list_id = os.environ.get("MAILCHIMP_LIST_ID")
    if not all([api_key, server, list_id]):
        raise EnvironmentError(
            "Missing one or more of MAILCHIMP_API_KEY, MAILCHIMP_SERVER, "
            "MAILCHIMP_LIST_ID as environment variables. See README.md."
        )
    base = f"https://{server}.api.mailchimp.com/3.0"
    auth = ("anystring", api_key)
    return api_key, server, list_id, base, auth


def normalize(name):
    return " ".join(name.strip().lower().split())


def describe_request_error(e):
    """Turn a requests exception into a specific, actionable message.
    Used by both the CLI and the GUI so error text stays consistent
    between the two."""
    if isinstance(e, requests.exceptions.HTTPError):
        status = e.response.status_code if e.response is not None else None
        if status == 401:
            return (
                "Mailchimp rejected the API key (401 Unauthorized). "
                "Double-check MAILCHIMP_API_KEY -- it may be wrong, revoked, "
                "or copied with extra whitespace."
            )
        if status == 403:
            return (
                "Mailchimp denied access (403 Forbidden). The API key may not "
                "have permission for this audience, or the account may be "
                "paused/suspended."
            )
        if status == 404:
            return (
                "Mailchimp returned 404 Not Found. This usually means "
                "MAILCHIMP_LIST_ID is wrong, or MAILCHIMP_SERVER doesn't match "
                "the data center in your API key (the part after the '-', "
                "e.g. 'us21')."
            )
        if status == 429:
            return "Mailchimp is rate-limiting requests (429). Wait a bit and try again."
        if status and 500 <= status < 600:
            return f"Mailchimp is having server issues (HTTP {status}). Try again shortly."
        return f"Mailchimp API error (HTTP {status}): {e}"
    if isinstance(e, requests.exceptions.ConnectionError):
        return (
            "Could not connect to Mailchimp. Check your internet connection "
            "and that MAILCHIMP_SERVER is correct (e.g. 'us21', not the full "
            "API key)."
        )
    if isinstance(e, requests.exceptions.Timeout):
        return "The request to Mailchimp timed out. Check your connection and try again."
    if isinstance(e, requests.exceptions.RequestException):
        return f"Network error talking to Mailchimp: {e}"
    return str(e)


def fetch_all_members(log=print):
    """Pull every audience member's name + email, handling pagination.
    Returns a dict of normalized name -> list of email addresses (a
    list because more than one contact can share a name). `log` is
    called after each page so callers can show progress on large
    audiences."""
    _, _, list_id, base, auth = _get_config()
    members = {}
    offset = 0
    count = 1000
    total_items = None
    while True:
        resp = requests.get(
            f"{base}/lists/{list_id}/members",
            auth=auth,
            params={
                "count": count,
                "offset": offset,
                "fields": "total_items,members.email_address,members.merge_fields,members.status",
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        if total_items is None:
            total_items = payload.get("total_items")
        batch = payload["members"]
        if not batch:
            break
        for m in batch:
            full_name = f"{m['merge_fields'].get('FNAME', '')} {m['merge_fields'].get('LNAME', '')}"
            key = normalize(full_name)
            members.setdefault(key, []).append(m["email_address"])
        offset += count
        fetched = min(offset, total_items) if total_items is not None else offset
        if total_items:
            log(f"  Fetched {fetched} of {total_items} contacts...")
        else:
            log(f"  Fetched {fetched} contacts...")
    return members


def get_or_create_tag(tag_name):
    """Return the segment id for a tag, creating it as a static segment
    if it doesn't already exist."""
    _, _, list_id, base, auth = _get_config()
    resp = requests.get(
        f"{base}/lists/{list_id}/segments",
        auth=auth,
        params={"count": 1000, "type": "static"},
    )
    resp.raise_for_status()
    for seg in resp.json()["segments"]:
        if seg["name"].strip().lower() == tag_name.strip().lower():
            return seg["id"]

    resp = requests.post(
        f"{base}/lists/{list_id}/segments",
        auth=auth,
        json={"name": tag_name, "static_segment": []},
    )
    resp.raise_for_status()
    return resp.json()["id"]


def run_tagging(csv_path, tag_name, log=print, dry_run=False):
    """
    Core workflow, factored out so both the CLI and a GUI can call it.
    `log` is a callable that receives each status line (defaults to
    print). When `dry_run` is True, matching runs normally but nothing
    is written to Mailchimp: no tag is created and no contacts are
    added to it. Returns a dict summary: {tagged, ambiguous, unmatched}.
    In dry-run mode, `tagged` reflects how many *would* be tagged.
    """
    _, _, list_id, base, auth = _get_config()

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or "name" not in reader.fieldnames:
            raise ValueError(
                f"CSV must have a column header exactly named 'name' "
                f"(lowercase). Found columns: {reader.fieldnames}"
            )
        requested_names = [row["name"] for row in reader if row.get("name", "").strip()]

    if not requested_names:
        log("No names found in the CSV under the 'name' column. Nothing to do.")
        return {"tagged": 0, "ambiguous": [], "unmatched": []}

    log(f"Fetching audience from Mailchimp (list {list_id})...")
    members = fetch_all_members(log=log)
    log(f"Loaded {len(members)} unique names from {sum(len(v) for v in members.values())} contacts.\n")

    matched_emails = []
    unmatched = []
    ambiguous = []

    for name in requested_names:
        key = normalize(name)
        candidates = members.get(key)
        if not candidates:
            unmatched.append(name)
        elif len(candidates) > 1:
            ambiguous.append((name, candidates))
        else:
            matched_emails.append(candidates[0])

    tagged_count = 0
    if matched_emails:
        if dry_run:
            tagged_count = len(matched_emails)
            log(
                f"[DRY RUN] Would tag {tagged_count} contact(s) with "
                f"\"{tag_name}\". Nothing was written to Mailchimp."
            )
        else:
            tag_id = get_or_create_tag(tag_name)
            resp = requests.post(
                f"{base}/lists/{list_id}/segments/{tag_id}",
                auth=auth,
                json={"members_to_add": matched_emails},
            )
            resp.raise_for_status()
            result = resp.json()
            tagged_count = result.get("total_added", len(matched_emails))
            log(f"Tagged {tagged_count} contacts with \"{tag_name}\".")
            if result.get("errors"):
                log("Some entries had errors:")
                for err in result["errors"]:
                    log(f"  {err}")
    else:
        log("No confident matches found -- nothing was tagged.")

    if ambiguous:
        log(f"\n{len(ambiguous)} name(s) matched more than one contact -- resolve manually:")
        for name, emails in ambiguous:
            log(f"  {name}: {', '.join(emails)}")

    if unmatched:
        log(f"\n{len(unmatched)} name(s) not found in the audience -- check spelling or add manually:")
        for name in unmatched:
            log(f"  {name}")

    return {"tagged": tagged_count, "ambiguous": ambiguous, "unmatched": unmatched}


def main():
    parser = argparse.ArgumentParser(
        prog="banana_tagger.py",
        description="Tag Mailchimp audience members by matching names from a CSV.",
    )
    parser.add_argument("csv_path", help="Path to a CSV with a 'name' column")
    parser.add_argument("tag_name", help='Tag name to apply, e.g. "Fall Classic 2026"')
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be tagged without writing anything to Mailchimp",
    )
    args = parser.parse_args()

    try:
        run_tagging(args.csv_path, args.tag_name, log=print, dry_run=args.dry_run)
    except EnvironmentError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: CSV file not found: {args.csv_path}")
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"Error: {describe_request_error(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
