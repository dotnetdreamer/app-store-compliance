---
name: plain-language
description: Write or refresh data/plain-language.json, the playbook's rules, deadlines, checklist boxes and checklist headings in everyday words, which Open Screenshot Generator's Store compliance view shows to people who are not developers or lawyers. Use after any change to data/rejection-patterns.json, data/regulatory-deadlines.json or docs/PRE-SUBMISSION-CHECKLIST.md (an upstream sync, a new rule, a moved date), when CI warns that plain-language entries are out of date, or when asked to make the playbook easier to read in the app.
---

# Plain words for the playbook

[Open Screenshot Generator](https://github.com/dotnetdreamer/open-screenshot-generator) reads this repository from GitHub and shows its rules in a view called Store compliance. The playbook is written for developers and lawyers, so the app shows the words in `data/plain-language.json` instead, with the playbook's own words one click away.

The app never writes that file. It is made here with `scripts/plain-language.py` and committed, and every copy of the app picks it up the next time someone opens the view.

## When to run it

- After syncing upstream changes into this fork.
- After editing a rule, a deadline, a checklist box or a checklist heading.
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

   Only new or changed entries go to the AI, so a run after a small change takes a minute or two. An entry that still fails the meaning check after three tries gets a plain title only if it's a rule or a deadline (see below), or is left out and listed at the end. The app shows the playbook's own words where the file has none.

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

Some rules and deadlines are too long to say in a few short lines without losing a condition. When a rule or a deadline still fails after three tries, the script writes a plain title for it alone, in rounds of its own: the title may leave details out, but it must not say anything the original doesn't, so a date or amount the original lacks fails in code, and a second AI pass lists anything else it gets wrong. The file keeps that entry with its other fields empty, and the app shows the plain title over the playbook's own words. `--status` counts these as plain titles only. App versions from before 2026-11-25 skip such an entry and show the playbook's words.

To check the meaning of every entry again, not only new ones, run this. It takes about 30 minutes for the whole playbook, so use it after changing the meaning checks:

```bash
python3 scripts/plain-language.py --recheck --verbose
```

To write every entry again from scratch, after changing the style rules or the prompts, run this. It takes about 40 minutes:

```bash
python3 scripts/plain-language.py --all --verbose
```

## Fixing an entry by hand

You can edit a plain entry yourself:

- Keep its `from` value. It is the fingerprint of the playbook words the entry was written from, and if it changes the app stops showing the entry.
- Keep the style, which is written for someone who has never read a store rule:
  - Everyday words. A technical term only in brackets after the everyday words, such as "add-on code (SDK)".
  - Sentences of 14 words at most. A short first line, then a list if there are several things, each list line starting with `- `.
  - A title of 7 words at most (a section title 6, a group heading 4), on one line, with no full stop.
  - No em or en dashes, no semicolons, no curly quotes, no exclamation marks and no Markdown.

`python3 scripts/test-plain-language.py` checks the style.

## Things to keep

- How `scripts/plain-language.py` reads the playbook and makes fingerprints must stay exactly what the app does. `scripts/test-plain-language.py` pins the values both sides compute, and the app's own tests pin the same ones. Change one side only together with the other.
- Commits here must not carry an AI author or an AI attribution line. `.github/workflows/no-ai-attribution.yml` fails the push if they do.
