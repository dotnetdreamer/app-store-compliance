---
name: plain-language
description: Write or refresh data/plain-language.json, the playbook's rules, deadlines and checklist boxes in everyday words, which Open Screenshot Generator's Store compliance view shows to people who are not developers or lawyers. Use after any change to data/rejection-patterns.json, data/regulatory-deadlines.json or docs/PRE-SUBMISSION-CHECKLIST.md (an upstream sync, a new rule, a moved date), when CI warns that plain-language entries are out of date, or when asked to make the playbook easier to read in the app.
---

# Plain words for the playbook

[Open Screenshot Generator](https://github.com/dotnetdreamer/open-screenshot-generator) reads this repository from GitHub and shows its rules in a view called Store compliance. The playbook is written for developers and lawyers, so the app shows the words in `data/plain-language.json` instead, with the playbook's own words one click away.

The app never writes that file. It is made here with `scripts/plain-language.py` and committed, and every copy of the app picks it up the next time someone opens the view.

## When to run it

- After syncing upstream changes into this fork.
- After editing a rule, a deadline or a checklist box.
- When CI prints "plain-language entries are out of date".

An out-of-date entry breaks nothing. The app shows the playbook's own words for it until the file is written again.

## Steps

1. Pull, so you work on what `main` has.
2. See what needs writing:

   ```bash
   python3 scripts/plain-language.py --status
   ```

3. Write it. This needs Claude Code installed and signed in. It runs on your own plan, with no tools:

   ```bash
   python3 scripts/plain-language.py --verbose
   ```

   Only new or changed entries go to the AI, so a run after a small change takes a minute or two. An entry that still fails the meaning check after three tries is left out and listed at the end. The app shows the playbook's own words for it.

4. Check the result, then read what changed with `git diff data/plain-language.json`:

   ```bash
   python3 scripts/test-plain-language.py
   ```

5. Commit `data/plain-language.json` and push to `main`.

## How the meaning is checked

Shorter words must not mean less, so a plain version is kept only when it passes two checks:

- In code, every date and every amount with a unit in the original has to appear in the plain version, with the same unit. "48 hours" written as "48 years" fails.
- A second AI pass lists any condition, exception, limit or group of people the plain version lost or changed, such as "when offered" or "at the same time". Its answer has to be a list of sentences. Any other answer is asked for again and never counts as a pass.

A version that fails is written again with the problems named.

To check every entry again, not only new ones, run this. It takes about 30 minutes for the whole playbook, so use it after changing the prompts or the checks:

```bash
python3 scripts/plain-language.py --recheck --verbose
```

## Fixing an entry by hand

You can edit a plain entry yourself:

- Keep its `from` value. It is the fingerprint of the playbook words the entry was written from, and if it changes the app stops showing the entry.
- Keep the style: everyday words, no em or en dashes, no curly quotes, no Markdown, and a title of at most 10 words with no full stop.

`python3 scripts/test-plain-language.py` checks the style.

## Things to keep

- How `scripts/plain-language.py` reads the playbook and makes fingerprints must stay exactly what the app does. `scripts/test-plain-language.py` pins the values both sides compute, and the app's own tests pin the same ones. Change one side only together with the other.
- Commits here must not carry an AI author or an AI attribution line. `.github/workflows/no-ai-attribution.yml` fails the push if they do.
