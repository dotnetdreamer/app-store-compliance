#!/usr/bin/env python3
"""Writes data/plain-language.json: every rule, deadline and checklist box of
this playbook in everyday words, for readers who are not developers or lawyers.
Open Screenshot Generator's Store compliance view downloads the file with the
rest of the playbook and shows these words, with the playbook's own one click
away. Nothing in the app writes this file: it is made here and committed.

  python3 scripts/plain-language.py            write what is new or changed
  python3 scripts/plain-language.py --status   say what is missing or out of date, write nothing
  python3 scripts/plain-language.py --recheck  check the meaning of every entry again
  python3 scripts/plain-language.py --all      write every entry again, from scratch

It covers the rules, the deadlines, the checklist's boxes, and the names and
short intros of the checklist's sections and groups.

The words have to be read once and understood: every sentence has 14 words or
fewer, a title 7 or fewer, several conditions become a short list, and jargon
("SDK", "runtime", "metadata") only appears in brackets after everyday words.
The script checks all of that in code and writes again whatever breaks it.

Run it after the playbook's rules, deadlines or checklist change, then commit
the file. Only entries that are new, or whose playbook words changed since
their plain words were written, go to the AI, so a run after a small change
takes a minute or two. It uses Claude Code on your own plan (claude -p, no
tools, no MCP servers, nothing saved); CLAUDE_BIN points at another copy.

Shorter words must not mean less. A plain version is kept only after its
meaning is checked: in code, every date and every amount with a unit of the
original has to be in it with the same unit ("48 hours" is not kept by "48
years"), and a second AI pass lists any condition, exception, limit or group
of people it lost or changed. That pass has to answer each entry with a list
of sentences; any other answer is asked for again and never counts as
approval. A failing version is written again with the problems named, up to
three rounds, then left out, so the app shows the playbook's own words for it.

Each rule and deadline entry keeps "from", a fingerprint of the playbook words
it was written from; a checklist box is keyed by the fingerprint of its own
words. The app compares them with its own fingerprints of the current words
and shows plain words only while they match. So how this script reads the
playbook and fingerprints it must stay exactly what the app does (plain.ts
and parse.ts of the app's src/lib/storeCompliance): scripts/test-plain-language.py
pins both with values the app's own tests pin too.
"""

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAIN_FILE = "data/plain-language.json"
RULES_FILE = "data/rejection-patterns.json"
DEADLINES_FILE = "data/regulatory-deadlines.json"
CHECKLIST_FILE = "docs/PRE-SUBMISSION-CHECKLIST.md"

KINDS = ("rules", "deadlines", "checklist", "sections", "groups")
FIELDS = {"rules": ["title", "problem", "fix"], "deadlines": ["title", "summary"], "checklist": ["text"], "sections": ["title", "intro"], "groups": ["title"]}
BATCH = {"rules": 10, "deadlines": 10, "checklist": 15, "sections": 10, "groups": 20}
AT_ONCE = 4
ROUNDS = 3
# The app leaves out a title longer than 120 characters and text longer than 600
# (its plain.ts). A title here stays well under that; text can use all of it.
TITLE_MAX = 80
TEXT_MAX = 600

# --- reading the playbook the way the app reads it ----------------------------------

# JavaScript's whitespace (String.prototype.trim and \s), which is not Python's.
JS_WS = "\t\n\v\f\r                  　﻿"
JS_WS_RUN = re.compile("[" + re.escape(JS_WS) + "]+")


def js_trim(value):
    return value.strip(JS_WS)


def hash32(value):
    """FNV-1a over UTF-16 code units, as hex: the app's hash32 (src/lib/i18n/hash.ts)."""
    h = 0x811C9DC5
    data = value.encode("utf-16-le", "surrogatepass")
    for i in range(0, len(data), 2):
        h ^= data[i] | (data[i + 1] << 8)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return format(h, "08x")


def text(value):
    return js_trim(value) if isinstance(value, str) else ""


def text_or_none(value):
    return text(value) or None


DAY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def day(value):
    value = text(value)
    return value if DAY.match(value) else None


def read_json(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        sys.exit(f"{path} is not a JSON object")
    return data


def read_rules():
    data = read_json(RULES_FILE)
    if not isinstance(data.get("patterns"), list):
        sys.exit(f'{RULES_FILE} has no "patterns" list')
    rules, seen = [], set()
    for raw in data["patterns"]:
        if not isinstance(raw, dict):
            continue
        rule_id, title = text(raw.get("id")), text(raw.get("title"))
        if not rule_id or not title or rule_id in seen:
            continue
        seen.add(rule_id)
        store = text(raw.get("platform")).lower()
        rules.append({
            "id": rule_id,
            "store": store if store in ("apple", "google", "both", "web") else "both",
            "guideline": text(raw.get("guideline")),
            "title": title,
            "detection": text(raw.get("detection")),
            "fix": text(raw.get("fix")),
        })
    return rules


def read_deadlines():
    data = read_json(DEADLINES_FILE)
    if not isinstance(data.get("deadlines"), list):
        sys.exit(f'{DEADLINES_FILE} has no "deadlines" list')
    deadlines, seen = [], set()
    for raw in data["deadlines"]:
        if not isinstance(raw, dict):
            continue
        deadline_id, law = text(raw.get("id")), text(raw.get("law"))
        if not deadline_id or not law or deadline_id in seen:
            continue
        seen.add(deadline_id)
        deadlines.append({
            "id": deadline_id,
            "jurisdiction": text(raw.get("jurisdiction")) or "Worldwide",
            "law": law,
            "requirement": text(raw.get("requirement")),
            "effective": day(raw.get("effective_date")),
            "grace": text_or_none(raw.get("grace_period")),
            "mandatory": day(raw.get("mandatory_date")),
            "enforcement": day(raw.get("enforcement_date")),
        })
    return deadlines


HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
BOX = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s+(.*)$")
GOES_ON = re.compile(r"^\s{2,}\S")
NEXT_BOX = re.compile(r"^\s*[-*]\s+\[")


def markdown_lines(source):
    body = re.sub(r"\r\n?", "\n", source)
    if body.startswith("---\n"):
        end = body.find("\n---", 4)
        if end != -1:
            body = body[body.find("\n", end + 1) + 1:]
    body = re.sub(r"<script[\s\S]*?</script>", "", body, flags=re.IGNORECASE)
    body = re.sub(r"<!--[\s\S]*?-->", "", body)
    return body.split("\n")


def checklist_key(words):
    """A box's key: the fingerprint of its words, spaces folded, lower case (the app's item id)."""
    return hash32(js_trim(JS_WS_RUN.sub(" ", words)).lower())


LABEL = re.compile(r"^[A-Z][A-Za-z0-9_-]*(?: [A-Za-z0-9_-]+){0,2}\.$")
ALWAYS = re.compile(r"never be marked (as )?(not applicable|n/a)", re.IGNORECASE)
BULLET = re.compile(r"^\s*[-*]\s+(.*)$")


def checklist_structure(source):
    """The checklist as the app's parseChecklist reads it: its boxes, and its sections and groups with their ids.

    A box counts only under a "##" section. A group starts at a "###" heading, or at a
    line of one to three words ending in a full stop ("Apple.") right before a list.
    A section's intro source is the raw lines between its heading and its first box
    or heading, spaces folded: what its plain words are fingerprinted against.
    """
    lines = markdown_lines(source)
    boxes, sections = [], []
    state = {"section": None, "group": None, "paragraph": [], "label": None, "always_next": False, "always": 0, "intro": None}

    def end_intro():
        if state["section"] is not None and state["intro"] is not None:
            state["section"]["intro_source"] = js_trim(JS_WS_RUN.sub(" ", "\n".join(state["intro"])))
        state["intro"] = None

    def start_group(title):
        section = state["section"]
        if section is None:
            return
        state["group"] = {"id": f"{section['id']}/{hash32(title or '')}", "title": title, "boxes": 0}
        section["groups"].append(state["group"])

    def flush():
        joined = js_trim(" ".join(state["paragraph"]))
        state["paragraph"] = []
        if not joined:
            return
        state["label"] = None
        if ALWAYS.search(joined):
            state["always_next"] = True
        if state["section"] is not None and len(joined) <= 25 and LABEL.match(joined):
            state["label"] = joined

    index = 0
    while index < len(lines):
        line = lines[index]
        head = HEADING.match(line)
        box = BOX.match(line)
        if head or box:
            end_intro()
        elif state["intro"] is not None:
            state["intro"].append(line)
        if head:
            flush()
            state["label"] = None
            state["always_next"] = False
            level, title = len(head.group(1)), js_trim(head.group(2))
            if level == 2:
                state["section"] = {"id": hash32(title), "title": title, "intro_source": "", "groups": []}
                sections.append(state["section"])
                state["group"] = None
                state["intro"] = []
            elif level > 2 and state["section"] is not None:
                start_group(title)
            index += 1
            continue
        if box and state["section"] is not None:
            flush()
            if state["label"]:
                start_group(state["label"][:-1])
                state["label"] = None
            body = box.group(1)
            # Lines indented under a box belong to it.
            while index + 1 < len(lines) and GOES_ON.match(lines[index + 1]) and not NEXT_BOX.match(lines[index + 1]):
                index += 1
                body += " " + js_trim(lines[index])
            if state["group"] is None:
                start_group(None)
            state["group"]["boxes"] += 1
            boxes.append({"key": checklist_key(body), "text": js_trim(body), "section": state["section"]["title"], "group": state["group"]["title"]})
            index += 1
            continue
        if not js_trim(line):
            flush()
            index += 1
            continue
        bullet = BULLET.match(line)
        if state["always_next"] and bullet:
            flush()
            state["always"] += 1
            index += 1
            continue
        if state["always_next"] and state["always"] > 0:
            state["always_next"] = False
        state["paragraph"].append(re.sub(r"^[-*]\s+", "", js_trim(line)))
        index += 1
    flush()
    end_intro()
    # As the app does: a group with no boxes, and a section with no groups, are left out.
    for section in sections:
        section["groups"] = [group for group in section["groups"] if group["boxes"] > 0]
    return boxes, [section for section in sections if section["groups"]]


def parse_checklist(source):
    """The boxes of the checklist as the app reads them."""
    return checklist_structure(source)[0]


def read_checklist_structure():
    path = os.path.join(ROOT, CHECKLIST_FILE)
    if not os.path.exists(path):
        return [], []
    with open(path, encoding="utf-8") as f:
        return checklist_structure(f.read())


def read_checklist():
    return read_checklist_structure()[0]


def section_fingerprint(section):
    """What a section's plain words were written from: its title and the lines under it (the app's sectionFingerprint)."""
    return hash32(section["title"] + "\n" + section["intro_source"])


def rule_fingerprint(rule):
    return hash32("\n".join([rule["store"], rule["guideline"], rule["title"], rule["detection"], rule["fix"]]))


def deadline_fingerprint(deadline):
    parts = [deadline["jurisdiction"], deadline["law"], deadline["requirement"]]
    parts += [deadline[name] or "" for name in ("effective", "grace", "mandatory", "enforcement")]
    return hash32("\n".join(parts))


# --- checking a plain version in code ------------------------------------------------

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
MONTH = "|".join(MONTHS)


def dates_of(value):
    """The dates of a text, as "12 September 2027", or "September 2027" with no day."""
    out = []

    def add(date):
        if date not in out:
            out.append(date)

    for m in re.finditer(r"\b([0-9]{4})-([0-9]{2})-([0-9]{2})\b", value):
        if 1 <= int(m.group(2)) <= 12:
            add(f"{int(m.group(3))} {MONTHS[int(m.group(2)) - 1]} {m.group(1)}")
    for m in re.finditer(rf"\b([0-9]{{1,2}}) ({MONTH}),? ([0-9]{{4}})\b", value):
        add(f"{int(m.group(1))} {m.group(2)} {m.group(3)}")
    for m in re.finditer(rf"\b({MONTH}) ([0-9]{{1,2}}),? ([0-9]{{4}})\b", value):
        add(f"{int(m.group(2))} {m.group(1)} {m.group(3)}")
    for m in re.finditer(rf"(?<![0-9] )\b({MONTH}) ([0-9]{{4}})\b", value):
        add(f"{m.group(1)} {m.group(2)}")
    return out


# Each unit by the words that write it; the money ones are written before the number too.
UNITS = {
    "percent": ["%", "percent", "per cent"],
    "day": ["days", "day"],
    "hour": ["hours", "hour"],
    "week": ["weeks", "week"],
    "month": ["months", "month"],
    "year": ["years", "year"],
    "minute": ["minutes", "minute"],
    "second": ["seconds", "second"],
    "thousand": ["thousand"],
    "million": ["million"],
    "billion": ["billion"],
    "employee": ["employees", "employee"],
    "user": ["users", "user"],
    "people": ["people"],
    "euro": ["euros", "euro", "EUR", "€"],
    "dollar": ["dollars", "dollar", "USD", "US$", "$"],
    "pound": ["pounds", "pound", "GBP", "£"],
}
BEFORE = ("euro", "dollar", "pound")
NUMBER = r"[0-9]+(?:[.,][0-9]+)*"
# Neither a letter nor a digit next to it, so "4%" and "48-hour" count and "48 hoursx" does not.
END = r"(?![^\W_])"


def words_of(unit):
    return "|".join(re.escape(word) for word in UNITS[unit])


ALL_AFTER = "|".join(words_of(unit) for unit in UNITS)
ALL_BEFORE = "|".join(words_of(unit) for unit in BEFORE)


def unit_of(word):
    lower = word.lower()
    return next(unit for unit, words in UNITS.items() if any(each.lower() == lower for each in words))


def plain_number(number):
    """"50,000" is 50000; "4.5" stays."""
    return re.sub(r"[,\s](?=[0-9]{3}(?![0-9]))", "", number)


def amounts_of(value):
    """Every amount with a unit: "48 hours", "4%", "17.5 million", "EUR 100", "€100"."""
    out = {}

    def add(number, word):
        amount = (plain_number(number), unit_of(word))
        out.setdefault(f"{amount[0]} {amount[1]}", amount)

    for m in re.finditer(rf"(?<![0-9.,])({NUMBER})(?:\s|-)?({ALL_AFTER}){END}", value, re.IGNORECASE):
        # "16-year-olds" is an age, not 16 years.
        if re.match(r"[-\s]?olds?\b", value[m.end():], re.IGNORECASE):
            continue
        add(m.group(1), m.group(2))
    for m in re.finditer(rf"({ALL_BEFORE})\s?({NUMBER})(?![0-9])", value, re.IGNORECASE):
        add(m.group(2), m.group(1))
    return list(out.values())


def has_amount(value, amount):
    """The amount is in the text, with the same unit, written either way round for money."""
    number, unit = amount
    flat = re.sub(r"([0-9])[,\s](?=[0-9]{3}(?![0-9]))", r"\1", value)
    if re.search(rf"(?<![0-9.,]){re.escape(number)}(?:\s|-)?(?:{words_of(unit)}){END}", flat, re.IGNORECASE):
        return True
    return unit in BEFORE and bool(re.search(rf"(?:{words_of(unit)})\s?{re.escape(number)}(?![0-9.,]?[0-9])", flat, re.IGNORECASE))


def lost_facts(original, plain):
    """The dates and amounts of the original the plain version does not have."""
    out = []
    kept = dates_of(plain)
    for date in dates_of(original):
        # "September 2027" is kept by "12 September 2027" too.
        if not any(each == date or each.endswith(" " + date) for each in kept):
            out.append(f"the date {date} is missing")
    for amount in amounts_of(original):
        if not has_amount(plain, amount):
            out.append(f"the amount {amount[0]} {amount[1]} is missing or has another unit")
    return out


def read_verdict(value):
    """The AI's verdict on one entry: its problems, [] when the meaning holds, None when it is not a list of sentences."""
    if not isinstance(value, list):
        return None
    if not all(isinstance(item, str) and item.strip() for item in value):
        return None
    return [item.strip() for item in value]


BANNED = re.compile(r"\b(ensure[sd]?|ensuring|crucial|robust|leverag\w*|seamless(?:ly)?|navigat\w*|landscape|delve\w*|utili[sz]\w*|comprehensive|facilitat\w*|in order to)\b", re.IGNORECASE)
MARKS = re.compile("[\u2013\u2014\u2018\u2019\u201c\u201d`*#!;]|\\]\\(")
# Words a reader who has never published an app trips on. Allowed only in brackets, after everyday words.
JARGON = re.compile(r"\b(run ?time|SDKs?|entitlements?|disclosures?|compliance|compliant|metadata|monetization|monetis\w+|monetiz\w+|jurisdictions?|pursuant|respectively|aforementioned|programmatic\w*|endpoints?|deprecat\w+|i\.e\.|e\.g\.)", re.IGNORECASE)
SENTENCE_WORDS = 14
TITLE_WORDS = {"rules": 7, "deadlines": 7, "checklist": 7, "sections": 6, "groups": 4}
TOTAL_WORDS = {"rules": 80, "deadlines": 80, "checklist": 60, "sections": 30, "groups": 4}
# A rule or a deadline has to keep every condition, date and amount, so it gets more room.
MAX_LINES = {"rules": 9, "deadlines": 9, "checklist": 7, "sections": 4, "groups": 1}


def sentences_of(value):
    """Each list line, and each sentence of every other line."""
    for line in value.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("- "):
            yield line[2:]
            continue
        for sentence in re.split(r"(?<=[.?!])\s+", line):
            if sentence.strip():
                yield sentence


def normalize(value):
    """Spaces folded within each line, empty lines dropped, line breaks kept (the app reads it so too)."""
    return "\n".join(line for line in (re.sub(r"[ \t]+", " ", raw).strip() for raw in value.split("\n")) if line)


def style_problems(kind, entry):
    """What breaks the house style, said so the writer can fix it."""
    if not isinstance(entry, dict):
        return ["missing"]
    out = []
    for field in FIELDS[kind]:
        value = normalize(entry.get(field)) if isinstance(entry.get(field), str) else ""
        if not value:
            if kind == "sections" and field == "intro":
                continue
            out.append(f'"{field}" is empty')
            continue
        if MARKS.search(value):
            out.append(f'"{field}" has a dash, a semicolon, a curly quote, Markdown or an exclamation mark')
        word = BANNED.search(value)
        if word:
            out.append(f'"{field}" uses "{word.group(0)}"')
        if value.count("(") > 2:
            out.append(f'"{field}" has more than two parts in brackets')
        jargon = JARGON.search(re.sub(r"\([^)]*\)", "", value))
        if jargon:
            out.append(f'"{field}" says "{jargon.group(0)}": use everyday words, with the term in brackets only if a developer needs it')
        if field == "title":
            if "\n" in value or len(value.split()) > TITLE_WORDS[kind] or len(value) > TITLE_MAX:
                out.append(f"the title has to be one line of {TITLE_WORDS[kind]} words or fewer")
            if value.endswith("."):
                out.append("the title ends with a full stop")
            continue
        lines = value.split("\n")
        if len(lines) > MAX_LINES[kind]:
            out.append(f'"{field}" has more than {MAX_LINES[kind]} lines')
        for sentence in sentences_of(value):
            count = len(sentence.split())
            if count > SENTENCE_WORDS:
                out.append(f'"{field}" has a sentence of {count} words ("{sentence[:60]}"): split it, {SENTENCE_WORDS} words at most')
        total = len(value.replace("- ", " ").split())
        if total > TOTAL_WORDS[kind]:
            out.append(f'"{field}" is {total} words: {TOTAL_WORDS[kind]} at most')
        if len(value) > TEXT_MAX:
            out.append(f'"{field}" is {len(value)} characters: {TEXT_MAX} at most')
    return out


# --- the prompts --------------------------------------------------------------------

WRITE_SYSTEM = """You rewrite parts of the App Store Compliance Playbook for people who make apps but have never read a store rule or a law: a founder, a designer, a marketer. They must understand every line the first time they read it, without reading it twice.

How to write:
- One idea per sentence. Every sentence has 14 words or fewer. Most have fewer than 10.
- Lead with what to do or what's wrong. No warm-up.
- When there are several steps, conditions or parts, write one short first line, then a list. Each list line starts with "- " and has 12 words or fewer.
- Use words a 12-year-old knows. Say "add-on code" not "SDK", "while it runs" not "at runtime", "store page text" not "metadata", "permission from Apple" not "entitlement", "notice" not "disclosure", "making money" not "monetization".
- A technical name a developer will need may follow in brackets, once per field at most: "Apple's privacy file (PrivacyInfo.xcprivacy)".
- Talk to the reader as "you" and "your app". Contractions are fine.
- Keep every condition, exception, date, number and amount, and exactly who it applies to. Put them in the list when one sentence can't hold them.
- Don't add facts or advice the original doesn't have. Leave out legal citations and article numbers.
- No semicolons, dashes, curly quotes, emoji, exclamation marks or Markdown. The only allowed formatting is "- " at the start of a list line.
- Never use: ensure, crucial, robust, leverage, seamless, navigate, landscape, delve, utilize, comprehensive, facilitate, "in order to".
- Write dates as 12 September 2027.
- Titles are one line, sentence case, no full stop.

Reply with one JSON object and nothing else. In a JSON string, write a line break as \n."""

KIND_PROMPT = {
    "rules": """Rewrite these rejection rules. For each id, return:
- "title": what gets the app turned down, 7 words or fewer.
- "problem": what the reviewer finds and why the store says no. One to three short sentences, or a short line and a list.
- "fix": what to do. One to three short sentences, or a short line and a list.

Example. The original:
{"id":"APPLE-PRIVACY-MANIFEST-MISSING","store":"App Store","guideline":"5.1.2","title":"Required reason APIs or third party SDKs without a privacy manifest","detection":"Required reason API usage or a commonly used third party SDK is present but no PrivacyInfo.xcprivacy is bundled, or an SDK lacks its signed manifest. Enforced by Apple at upload since 2024.","fix":"Add a PrivacyInfo.xcprivacy with NSPrivacyAccessedAPITypes and approved reason codes, list NSPrivacyTrackingDomains, and confirm every third party SDK ships its signed manifest."}
In plain words:
{"APPLE-PRIVACY-MANIFEST-MISSING":{"title":"Missing the privacy file Apple asks for","problem":"Your app, or add-on code in it, uses phone features Apple watches.\\nIt has no privacy file saying why.\\nSince 2024, Apple blocks these uploads.","fix":"Ask your developer to add Apple's privacy file (PrivacyInfo.xcprivacy).\\n- Give an approved reason for each feature used.\\n- List any web addresses used for tracking.\\n- Check every add-on ships its own signed file."}}""",
    "deadlines": """Rewrite these deadlines: dates when a new law or store rule starts to apply to apps. For each id, return:
- "title": what changes for apps, 7 words or fewer. Not the law's name.
- "summary": who it applies to and what they must do, with every date the requirement names. One to three short sentences, or a short line and a list. The app shows the place, the law's name and the grace period beside it, so leave those out.

Example. The original:
{"id":"US-IL-BIPA","where":"Illinois (USA State)","law":"Biometric Information Privacy Act (BIPA), 740 ILCS 14","requirement":"Written notice and release before collecting biometric identifiers, public retention schedule, destruction within 3 years, and no sale."}
In plain words:
{"US-IL-BIPA":{"title":"Ask before collecting face or fingerprint data","summary":"If your app collects face scans, fingerprints or similar data:\\n- Tell people in writing and get their OK first.\\n- Publish how long you keep it.\\n- Delete it within 3 years.\\n- Never sell it."}}""",
    "checklist": """Rewrite these checklist boxes: checks to make before sending an app for review. For each id, return:
- "text": the check, said as something that's true once it's done. A first line of 14 words or fewer. Add a short list only for details the first line can't hold. Keep a store guideline number or a date in brackets when the original has one.

Example. The original:
{"id":"a1b2c3d4","section":"Shared (both stores)","group":"Mobile Security and Best Practices","text":"**Secure Storage:** Sensitive items (session tokens, passwords, keys) are stored securely (iOS Keychain or Android EncryptedSharedPreferences/Keystore) and never in plaintext files, plain SharedPreferences, or unencrypted local databases."}
In plain words:
{"a1b2c3d4":{"text":"Passwords, logins and keys are stored safely.\\n- Keep them in the phone's secure storage (iOS Keychain, Android Keystore).\\n- Never in plain files or unprotected databases."}}""",
    "sections": """Rewrite these checklist section headings and the short text under them. The reader sees them inside an app, not as a document. For each id, return:
- "title": the section's name, 6 words or fewer.
- "intro": one or two short sentences that help the reader, such as when to skip the section. Use "" when the original text only points to another document or says nothing the reader needs.

Example. The original:
{"id":"s1","title":"EU specific (legal layer, if the app reaches EU users)","intro":"Passing App Review does not make an app EU-legal. The full hard rules and sources are in the [EU regulatory guide](EU-REGULATORY-2026.md)."}
In plain words:
{"s1":{"title":"EU laws","intro":"Only if people in the EU can get your app.\\nPassing App Review doesn't make your app legal in the EU."}}""",
    "groups": """Rewrite these group names inside checklist sections. For each id, return:
- "title": the group's name in 1 to 4 everyday words.

Example. The original:
{"id":"g1","section":"Shared (both stores)","title":"Metadata and listing"}
In plain words:
{"g1":{"title":"Your store page"}}""",
}

CHECK_SYSTEM = """You check plain-English versions of App Store Compliance Playbook entries against the originals. People will act on the plain version alone, so it has to mean the same.

For each id, list every way the plain version changes the meaning:
- a requirement, condition, exception or qualifier that is missing or weaker, such as "when offered", "at the same time", "only if", "unless", "at least", "before", "every"
- a date, deadline, number, limit or amount that is missing or different
- who or where it applies to, missing, wider or narrower
- anything the original does not say
Do not list: legal citations, article and regulation numbers left out on purpose, technical names moved into brackets, everyday words used for technical terms, a sentence split into short ones or into a list, a store's own name for something, or wording and tone. The app shows a deadline's place, the law's name and its grace period, and a rule's store and guideline, beside the plain words, so do not list those as missing either. A section's text may drop a pointer to another document.

Reply with one JSON object and nothing else: {"<id>": ["what is lost or changed", ...]}, with an empty list for a version that means the same."""

# The fields a plain version stands in for. The place, the law's name, the grace period and the guideline show beside it.
REPLACED = {"rules": ["title", "detection", "fix"], "deadlines": ["requirement"], "checklist": ["text"], "sections": ["title", "intro"], "groups": ["title"]}


def write_prompt(kind, jobs, feedback):
    lines = [json.dumps({"id": job["id"], **job["original"]}, ensure_ascii=False) for job in jobs]
    notes = [job for job in jobs if job["id"] in feedback]
    fix = ""
    if notes:
        fix = "\n\nAn earlier version of these lost or changed meaning. Fix each problem and keep everything else:\n" + "\n".join(
            f"- {job['id']}: {'; '.join(feedback[job['id']])}" for job in notes
        )
    shape = ", ".join(f'"{field}": "..."' for field in FIELDS[kind])
    noun = "boxes" if kind == "checklist" else kind
    return f"{KIND_PROMPT[kind]}\n\nThe {noun}, one JSON object a line:\n" + "\n".join(lines) + fix + f'\n\nReply as {{"<id>": {{{shape}}}}} with every id above.'


def check_prompt(jobs, entries):
    lines = [json.dumps({"id": job["id"], "original": job["original"], "plain": entries[job["id"]]}, ensure_ascii=False) for job in jobs]
    return "The entries, one JSON object a line:\n" + "\n".join(lines) + '\n\nReply as {"<id>": [...]} with every id above.'


# --- asking Claude Code ----------------------------------------------------------------

def claude_binary():
    if os.environ.get("CLAUDE_BIN"):
        return os.environ["CLAUDE_BIN"]
    local = os.path.expanduser("~/.local/bin/claude")
    return local if os.path.exists(local) else (shutil.which("claude") or "claude")


def ask_claude(system, user, model):
    """One prompt, the way the app's one-shot prompts run: no tools, no MCP servers, nothing saved, the plan Claude Code is signed in with."""
    work = tempfile.mkdtemp(prefix="plain-language-")
    try:
        with open(os.path.join(work, "system.md"), "w", encoding="utf-8") as f:
            f.write(system)
        with open(os.path.join(work, "settings.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        flags = ["-p", "--output-format", "json", "--setting-sources", "", "--settings", os.path.join(work, "settings.json"), "--tools", "",
                 "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence", "--permission-mode", "dontAsk",
                 "--system-prompt-file", os.path.join(work, "system.md")]
        if model:
            flags += ["--model", model]
        env = dict(os.environ, CLAUDE_CODE_DISABLE_CLAUDE_MDS="1", NO_COLOR="1")
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_ENTRYPOINT"):
            env.pop(name, None)
        done = subprocess.run([claude_binary(), *flags], input=user, capture_output=True, text=True, cwd=work, env=env, timeout=900)
        try:
            reply = json.loads(done.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f"Claude Code stopped (exit {done.returncode}): {done.stderr[:400]}")
        if reply.get("is_error") or not isinstance(reply.get("result"), str):
            raise RuntimeError(f"Claude Code stopped: {reply.get('result') or 'no result'}")
        return reply["result"]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def read_reply(value):
    body = re.sub(r"^```[a-z]*\s*|\s*```$", "", value.strip(), flags=re.IGNORECASE).strip()
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("the reply has no JSON object")
    reply = json.loads(body[start:end + 1])
    if not isinstance(reply, dict):
        raise ValueError("the reply is not a JSON object")
    return reply


# --- one run -----------------------------------------------------------------------------

PRINT = threading.Lock()


def say(*parts):
    with PRINT:
        print(*parts, flush=True)


def original_text(job):
    return " \n ".join(job["original"].get(field, "") for field in REPLACED[job["kind"]])


class Run:
    def __init__(self, args):
        self.args = args
        self.existing = self.read_existing()
        rules, deadlines = read_rules(), read_deadlines()
        boxes, sections = read_checklist_structure()
        stores = {"apple": "App Store", "google": "Google Play", "both": "Both stores", "web": "Websites and web apps"}
        unique_boxes = {}
        for box in boxes:
            unique_boxes[box["key"]] = box
        self.jobs = {
            "rules": [{"kind": "rules", "id": r["id"], "from": rule_fingerprint(r),
                       "original": {"store": stores[r["store"]], "guideline": r["guideline"], "title": r["title"], "detection": r["detection"], "fix": r["fix"]}} for r in rules],
            "deadlines": [{"kind": "deadlines", "id": d["id"], "from": deadline_fingerprint(d),
                           "original": {"where": d["jurisdiction"], "law": d["law"], "requirement": d["requirement"], **({"grace": d["grace"]} if d["grace"] else {})}} for d in deadlines],
            "checklist": [{"kind": "checklist", "id": b["key"], "from": "",
                           "original": {"section": b["section"], **({"group": b["group"]} if b["group"] else {}), "text": b["text"]}} for b in unique_boxes.values()],
            "sections": [{"kind": "sections", "id": section["id"], "from": section_fingerprint(section),
                          "original": {"title": section["title"], "intro": section["intro_source"]}} for section in sections],
            "groups": [{"kind": "groups", "id": group["id"], "from": "",
                        "original": {"section": section["title"], "title": group["title"]}} for section in sections for group in section["groups"] if group["title"]],
        }

    @staticmethod
    def read_existing():
        path = os.path.join(ROOT, PLAIN_FILE)
        if not os.path.exists(path):
            return {kind: {} for kind in KINDS}
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {kind: data.get(kind) if isinstance(data.get(kind), dict) else {} for kind in KINDS}

    def fresh(self, job):
        """The file has a version of this entry written from the playbook's current words."""
        if self.args.all:
            return False
        entry = self.existing[job["kind"]].get(job["id"])
        if not isinstance(entry, dict):
            return False
        if job["from"] and entry.get("from") != job["from"]:
            return False
        needed = [field for field in FIELDS[job["kind"]] if not (job["kind"] == "sections" and field == "intro")]
        return all(isinstance(entry.get(field), str) and entry[field] for field in needed)

    def check_meaning(self, jobs, entries):
        """The AI's reading of each version: its problems, [] when the meaning holds, or None when, asked three times, it never answered that entry with a list of sentences."""
        out, ask = {}, list(jobs)
        for attempt in range(1, 4):
            if not ask:
                break
            verdicts = {}
            try:
                verdicts = read_reply(ask_claude(CHECK_SYSTEM, check_prompt(ask, entries), self.args.model))
            except Exception as error:  # noqa: BLE001 - an unreadable answer is asked again
                say(f"  meaning check: {error}")
            again = []
            for job in ask:
                said = read_verdict(verdicts.get(job["id"]))
                if said is None:
                    again.append(job)
                else:
                    out[job["id"]] = said
            if self.args.verbose and again:
                say(f"  meaning check, attempt {attempt}: no readable answer for {', '.join(job['id'] for job in again)}")
            ask = again
        for job in ask:
            out[job["id"]] = None
        return out

    def produce(self, kind, jobs):
        """One batch: write what has no version yet, check every version's meaning, write again what fails."""
        kept, current, feedback = {}, {}, {}
        for job in jobs:
            if self.fresh(job):
                current[job["id"]] = self.existing[kind][job["id"]]
        for round_number in range(1, ROUNDS + 1):
            to_write = [job for job in jobs if job["id"] not in kept and job["id"] not in current]
            if to_write:
                try:
                    reply = read_reply(ask_claude(WRITE_SYSTEM, write_prompt(kind, to_write, feedback), self.args.model))
                    for job in to_write:
                        problems = style_problems(kind, reply.get(job["id"]))
                        if problems:
                            feedback[job["id"]] = problems
                        else:
                            current[job["id"]] = {field: normalize(reply[job["id"]].get(field) or "") for field in FIELDS[kind]}
                except Exception as error:  # noqa: BLE001 - the round is tried again
                    say(f"  {kind}: {error}")
            to_check = [job for job in jobs if job["id"] not in kept and job["id"] in current]
            if not to_check:
                continue
            verdicts = self.check_meaning(to_check, current)
            # A version that failed the style check was never checked, so its
            # problems go with it into the next round.
            checked = {job["id"] for job in to_check}
            feedback = {job_id: problems for job_id, problems in feedback.items() if job_id not in checked}
            for job in to_check:
                said = verdicts.get(job["id"])
                plain = " \n ".join(current[job["id"]].values())
                problems = lost_facts(original_text(job), plain) + (said if said is not None else ["the meaning check gave no readable answer for this entry"])
                if self.args.verbose and problems:
                    say(f"  {kind} {job['id']}, round {round_number}: {'; '.join(problems)}")
                if problems:
                    feedback[job["id"]] = problems
                    del current[job["id"]]
                else:
                    kept[job["id"]] = current[job["id"]]
        left = {job["id"]: feedback.get(job["id"], ["no answer"]) for job in jobs if job["id"] not in kept}
        return kept, left

    def main(self):
        args = self.args
        kinds = [args.only] if args.only else list(KINDS)
        todo = []
        for kind in kinds:
            waiting = [job for job in self.jobs[kind] if not self.fresh(job)]
            chosen = (self.jobs[kind] if args.recheck else waiting)[: args.limit]
            todo += chosen
            print(f"{kind}: {len(self.jobs[kind])} in the playbook, {len(self.jobs[kind]) - len(waiting)} in plain words, {len(waiting)} missing or out of date")
            if args.status and waiting:
                print("  " + ", ".join(job["id"] for job in waiting))
        if args.status:
            return 0
        if not todo:
            print("Nothing to write.")
            return 0

        batches = []
        for kind in kinds:
            jobs = [job for job in todo if job["kind"] == kind]
            batches += [(kind, jobs[i:i + BATCH[kind]]) for i in range(0, len(jobs), BATCH[kind])]
        results = {kind: {} for kind in KINDS}
        left, finished = [], 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=AT_ONCE) as pool:
            futures = {pool.submit(self.produce, kind, jobs): kind for kind, jobs in batches}
            for future in concurrent.futures.as_completed(futures):
                kind = futures[future]
                try:
                    kept, missed = future.result()
                except Exception as error:  # noqa: BLE001 - one broken batch must not lose the others
                    kept, missed = {}, {"a batch": [str(error)]}
                results[kind].update(kept)
                left += [f"  {kind} {job_id}: left in the playbook's words ({'; '.join(problems)})" for job_id, problems in missed.items()]
                finished += 1
                say(f"  {finished} of {len(batches)} batches done")
        for line in left:
            print(line, file=sys.stderr)

        # The file in the playbook's order. An entry this run worked on is kept only
        # if it passed the meaning check; one it did not touch stays as it was.
        worked = {(job["kind"], job["id"]) for job in todo}
        out = {
            "version": 1,
            "about": "The rules, deadlines and checklist boxes of this playbook in everyday words, for people who are not developers or lawyers, "
                     "written with scripts/plain-language.py and each one checked against its original for meaning. \"from\" is a fingerprint of "
                     "the original an entry was written from: when the original changes, apps show the original until this file is written again.",
        }
        total = 0
        for kind in KINDS:
            section = {}
            for job in self.jobs[kind]:
                entry = results[kind].get(job["id"])
                if entry is None and (kind, job["id"]) not in worked and self.fresh(job):
                    entry = self.existing[kind][job["id"]]
                if entry is None:
                    continue
                if kind in ("checklist", "groups"):
                    section[job["id"]] = {field: entry[field] for field in FIELDS[kind]}
                else:
                    section[job["id"]] = {"from": job["from"], **{field: entry.get(field, "") for field in FIELDS[kind]}}
                total += 1
            out[kind] = section
        print(f"{len(left)} left in the playbook's words")
        if args.dry_run:
            print(json.dumps(out, indent=2, ensure_ascii=False))
            return 0
        with open(os.path.join(ROOT, PLAIN_FILE), "w", encoding="utf-8") as f:
            f.write(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
        print(f"Wrote {PLAIN_FILE}: {total} entries in plain words")
        return 0


def main():
    parser = argparse.ArgumentParser(description="Write data/plain-language.json, the playbook in everyday words.")
    parser.add_argument("--status", action="store_true", help="say what is missing or out of date, write nothing")
    parser.add_argument("--recheck", action="store_true", help="check the meaning of every entry in the file again, not only new ones")
    parser.add_argument("--all", action="store_true", help="write every entry again from scratch, after a change to the style or the prompts")
    parser.add_argument("--only", choices=KINDS, help="one kind only")
    parser.add_argument("--limit", type=int, default=None, help="at most this many entries of each kind, to try the prompt")
    parser.add_argument("--dry-run", action="store_true", help="print the file instead of writing it")
    parser.add_argument("--verbose", action="store_true", help="print every problem the meaning check finds")
    parser.add_argument("--model", help="a model other than Claude Code's default")
    return Run(parser.parse_args()).main()


if __name__ == "__main__":
    sys.exit(main())
