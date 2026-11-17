#!/usr/bin/env python3
"""Tests scripts/plain-language.py and data/plain-language.json.

The fingerprints are a contract with Open Screenshot Generator, which shows a
plain entry only while its "from" matches its own fingerprint of the playbook's
current words. The values below are the ones the app computes for the same
input (its tests/unit/storeCompliance.test.ts pins them too): if either side
changes how it reads or fingerprints the playbook, one of the two fails.

The meaning checks repeat the cases an audit found slipping through: "4%",
"EUR 100", "48 hours" kept as "48 years", and a verdict of [null, {...}].

An entry that is only out of date (the playbook changed after it was written)
is a warning, not a failure: the app shows the playbook's own words for it
until the next run of scripts/plain-language.py.
"""

import importlib.util
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("plain_language", os.path.join(ROOT, "scripts", "plain-language.py"))
plain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plain)


class Fingerprints(unittest.TestCase):
    def test_rules_match_the_app(self):
        self.assertEqual(plain.rule_fingerprint({"store": "apple", "guideline": "2.1", "title": "No demo account for the reviewer",
                                                 "detection": "A login with no demo account in the notes.", "fix": "Put a working demo account in the review notes."}), "4e5dba76")
        self.assertEqual(plain.rule_fingerprint({"store": "both", "guideline": "", "title": "Café ✓ 😀 “quotes”",
                                                 "detection": "Naïve résumé\ttab", "fix": ""}), "6e9361c5", "UTF-16 code units, as JavaScript counts them")

    def test_deadlines_match_the_app(self):
        self.assertEqual(plain.deadline_fingerprint({"jurisdiction": "European Union (EU)", "law": "EU AI Act Article 50", "requirement": "Tell people they are talking to an AI.",
                                                     "effective": "2024-08-01", "grace": "24 months", "mandatory": "2026-08-02", "enforcement": "2026-08-02"}), "8294a387")
        self.assertEqual(plain.deadline_fingerprint({"jurisdiction": "Worldwide", "law": "A law", "requirement": "",
                                                     "effective": None, "grace": None, "mandatory": None, "enforcement": None}), "aa227a91")

    def test_checklist_boxes_match_the_app(self):
        source = ("# Checklist\n\n- [ ] Before any section: not a box.\n\n## Start here\n\n- [ ] A working **demo** account is in the review notes.\n"
                  "- [x] A box that goes on\n  over two lines, with  extra   spaces.\n\n### Group\n\n* [ ] Café ✓ 😀 box.\n")
        boxes = [box["key"] for box in plain.parse_checklist(source)]
        self.assertEqual(boxes, ["2dddae10", "35b8c0d1", "3ac9ecd7"])


class MeaningChecks(unittest.TestCase):
    def amounts(self, value):
        return [f"{number} {unit}" for number, unit in plain.amounts_of(value)]

    def test_amounts_keep_their_units(self):
        self.assertEqual(self.amounts("A fine of 4% of turnover"), ["4 percent"])
        self.assertEqual(self.amounts("up to EUR 100 a day"), ["100 euro"])
        self.assertEqual(self.amounts("within 48 hours"), ["48 hour"])
        self.assertEqual(self.amounts("17.5 million pounds or 4 percent"), ["17.5 million", "4 percent"])
        self.assertEqual(self.amounts("€100, $4.99 and £5"), ["100 euro", "4.99 dollar", "5 pound"])
        self.assertEqual(self.amounts("50,000 users and a 24-hour window"), ["50000 user", "24 hour"])
        self.assertEqual(self.amounts("Article 50(2), 16 CFR Part 312, API level 36"), [])
        self.assertEqual(self.amounts("children and 16-year-olds"), [], "an age is not a length of time")

    def test_a_lost_amount_or_unit_is_caught(self):
        missing = "the amount 48 hour is missing or has another unit"
        self.assertEqual(plain.lost_facts("Remove it within 48 hours.", "Remove it within 48 years."), [missing])
        self.assertEqual(plain.lost_facts("Remove it within 48 hours.", "Take it down in 48-hour time."), [])
        self.assertEqual(plain.lost_facts("A fine of 4%.", "A fine of 4 percent."), [])
        self.assertEqual(plain.lost_facts("A fine of 4%.", "A fine of 40%."), ["the amount 4 percent is missing or has another unit"])
        self.assertEqual(plain.lost_facts("A fee of EUR 100.", "A fee of 100 euros."), [])
        self.assertEqual(plain.lost_facts("A fee of EUR 100.", "A fee of €100."), [])
        self.assertEqual(plain.lost_facts("A fee of EUR 100.", "A fee of 100."), ["the amount 100 euro is missing or has another unit"])
        self.assertEqual(plain.lost_facts("A fee of EUR 100.", "A fee of USD 100."), ["the amount 100 euro is missing or has another unit"])
        self.assertEqual(plain.lost_facts("More than 50,000 users.", "More than 50000 users."), [])

    def test_a_lost_date_is_caught(self):
        self.assertEqual(sorted(plain.dates_of("From 2027-09-12, and by 1 October 2026, or in March 2027.")), ["1 October 2026", "12 September 2027", "March 2027"])
        self.assertEqual(plain.lost_facts("Applies from 2027-09-12.", "Applies from 12 September 2027."), [])
        self.assertEqual(plain.lost_facts("Due in September 2027.", "Due on 12 September 2027."), [])
        self.assertEqual(plain.lost_facts("Contracts must comply by 12 September 2027.", "Older contracts must comply."), ["the date 12 September 2027 is missing"])

    def test_only_a_list_of_sentences_is_a_verdict(self):
        self.assertEqual(plain.read_verdict([]), [])
        self.assertEqual(plain.read_verdict(['Drops "when offered".']), ['Drops "when offered".'])
        self.assertIsNone(plain.read_verdict([None, {"issue": "The condition was omitted."}]), "the audit case: not an approval")
        self.assertIsNone(plain.read_verdict(["A problem", 5]))
        self.assertIsNone(plain.read_verdict([""]))
        self.assertIsNone(plain.read_verdict("ok"))
        self.assertIsNone(plain.read_verdict(None))
        self.assertIsNone(plain.read_verdict({}))


class TheFile(unittest.TestCase):
    def test_the_file_is_what_the_app_reads(self):
        path = os.path.join(ROOT, plain.PLAIN_FILE)
        if not os.path.exists(path):
            self.skipTest("no data/plain-language.json yet")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIsInstance(data, dict)
        rules = {r["id"]: r for r in plain.read_rules()}
        deadlines = {d["id"]: d for d in plain.read_deadlines()}
        boxes = {b["key"] for b in plain.read_checklist()}
        stale = []
        for kind in plain.KINDS:
            entries = data.get(kind, {})
            self.assertIsInstance(entries, dict, kind)
            for entry_id, entry in entries.items():
                self.assertEqual(plain.style_problems(kind, entry), [], f"{kind} {entry_id}")
                if kind == "rules":
                    if entry_id not in rules or entry.get("from") != plain.rule_fingerprint(rules[entry_id]):
                        stale.append(f"{kind} {entry_id}")
                elif kind == "deadlines":
                    if entry_id not in deadlines or entry.get("from") != plain.deadline_fingerprint(deadlines[entry_id]):
                        stale.append(f"{kind} {entry_id}")
                elif entry_id not in boxes:
                    stale.append(f"{kind} {entry_id}")
        if stale:
            # Out of date is fine for the app (it shows the playbook's words), but say so.
            print(f"::warning::{len(stale)} plain-language entries are out of date. Run python3 scripts/plain-language.py and commit the file.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
