# Banana Tagger

A command-line tool (with an optional GUI) for tagging Mailchimp
audience members from a CSV that has **names only, no email
addresses**.

Mailchimp's own CSV import can tag contacts, but only by matching on
email. This tool exists for the opposite case: you have a list of full
names from somewhere that was never tied to Mailchimp in the first
place (an event roster, a sign-in sheet, a waitlist a front desk kept
by hand) and you need those people tagged in your existing audience
without typing each name into search one at a time.

If your source list already has emails, you don't need this, use
Mailchimp's native import instead. This tool is specifically for when
all you have is a name.

## How it works

1. Pulls your full Mailchimp audience (name + email), handling
   pagination.
2. Normalizes and matches each name in your CSV against that audience.
3. Creates the tag (as a static segment) if it doesn't already exist,
   and batch-adds every confidently matched contact in one API call.
4. Prints two lists for manual follow-up instead of guessing:
   - **Ambiguous matches** - more than one contact shares that name.
   - **Unmatched names** - not found in the audience (misspelling,
     not yet subscribed, etc.).

Ambiguous matches are never auto-resolved. If your audience has two
contacts with the same name (two emails for one person, or two
different people who share a name), the tool flags it rather than
risking a tag on the wrong contact.

## Setup

```bash
git clone https://github.com/animalrap/banana-tagger.git
cd banana-tagger
pip install -r requirements.txt
```

1. Get your Mailchimp API key: **Account > Extras > API keys**.
   The data center prefix is the text after the `-` in the key
   (e.g. key `abc123-us21` means server = `us21`).
2. Get your Audience/List ID: **Audience > Settings > Audience name
   and defaults**.
3. Set three environment variables (do not hardcode these, and do not
   commit them):

   ```bash
   export MAILCHIMP_API_KEY="your-key-here"
   export MAILCHIMP_SERVER="us21"
   export MAILCHIMP_LIST_ID="your-list-id"
   ```

   On Windows PowerShell:
   ```powershell
   $env:MAILCHIMP_API_KEY = "your-key-here"
   $env:MAILCHIMP_SERVER = "us21"
   $env:MAILCHIMP_LIST_ID = "your-list-id"
   ```

   To persist these across sessions on Windows, set them as permanent
   System Environment Variables instead of re-entering them each time.
   See `.env.example` for a reference of what's needed.

## Usage

Your CSV needs exactly one column, headed `name` (lowercase), with one
full name per row formatted the same way it appears in Mailchimp
("First Last"). See `sample_names.csv`.

```bash
python banana_tagger.py names.csv "Fall Classic 2026"
```

Redirect output to a file if you want a saved record:

```bash
python banana_tagger.py names.csv "Fall Classic 2026" > results.txt
```

### Preview before tagging (dry run)

Add `--dry-run` to see the matched, ambiguous, and unmatched counts
without creating the tag or writing anything to Mailchimp. Useful for
checking a new CSV before it touches your live audience:

```bash
python banana_tagger.py names.csv "Fall Classic 2026" --dry-run
```

In the GUI, check the "Dry run" box before clicking Run, the button
relabels to **Preview (Dry Run)** so it's clear nothing will be written.

### Optional GUI

```bash
python gui.py
```

Gives you a file picker, a tag-name field, and a live output pane, so
non-technical staff can run it without touching a terminal. To package
it as a double-clickable app with no visible console window on
Windows:

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "Banana Tagger" gui.py
```

## Limitations

- Matching is by full name only (no email, member ID, etc. required
  in the source CSV), so it can't disambiguate two different people
  who happen to share a name, that's why ambiguous matches are
  flagged for manual review rather than resolved automatically.
- Requires the name in your CSV to match the FNAME/LNAME merge fields
  in Mailchimp closely enough after normalization (case and extra
  whitespace are ignored, but not spelling differences or nicknames).
- Uses Mailchimp's static-segment tagging, which is the standard
  approach for name-based ad hoc tags, not the newer contact-tags
  endpoint. Static segments work fine for building a send list and
  show up in a similar way, but if you're already relying on
  Mailchimp's separate tags feature elsewhere, be aware these are
  distinct systems.

## License

MIT, see [LICENSE](LICENSE).
