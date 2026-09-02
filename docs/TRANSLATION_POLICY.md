# Documentation Translation Policy

> **Language:** English is the source of truth. [Read this policy in
> Vietnamese](./TRANSLATION_POLICY.vi.md).

## 1. Authority

The English document is authoritative. A Vietnamese file is a reading aid
derived from its English counterpart. If the two disagree, the English text
wins immediately; the Vietnamese file must then be corrected.

Translations never introduce, remove, relax, or strengthen a business rule,
phase boundary, acceptance criterion, UI requirement, deployment control, or
operational procedure.

## 2. Layout and naming

- Root `README.md` maps to `README.vi.md`.
- `docs/<path>/<NAME>.md` maps to `docs/<path>/<NAME>.vi.md` — the Vietnamese
  mirror sits in the same folder as its English source, distinguished only by
  the `.vi.md` suffix (for example `docs/GUI_DESIGN.md` ↔ `docs/GUI_DESIGN.vi.md`,
  `docs/deployment/VPS.md` ↔ `docs/deployment/VPS.vi.md`). There is no separate
  Vietnamese subfolder.
- Every English document links to its mirror from its language header, and
  every mirror links back to its canonical `.md` source; a documentation index
  lists both columns.
- File names, section numbers, code, identifiers, API paths, database objects,
  commands, environment variables, barcode formats, enum values, and literal UI
  copy remain in English unless the English source explicitly defines a
  localized value.
- Vietnamese prose may retain an English domain term when translating it would
  make the PartFlow vocabulary ambiguous.

## 3. Required translation header

Every Vietnamese mirror must identify:

1. its English source path;
2. the upstream repository commit used as the documentation baseline (or the
   package revision when the English source was created after that baseline);
3. that English remains authoritative.

The baseline is evidence of synchronization, not a claim that a newly authored
English file existed in that upstream commit and not a substitute for review.

Vietnamese mirrors are complete **semantic** translations, not mechanical
line-by-line copies. Repeated explanation and superseded change-history detail
may be consolidated in Vietnamese, but every active normative requirement and
every numbered section must remain represented with the same strength.

## 4. Update workflow

When an English document changes:

1. Change the English source first.
2. Review the diff against the source commit recorded in the Vietnamese file.
3. Translate every material change without changing its meaning.
4. Preserve section numbering and technical literals.
5. Update the recorded baseline/package revision only after the mirror is complete.
6. Run the documentation checks in §5.

A stale translation must be marked clearly at the top until it is synchronized.
Never leave it appearing current.

## 5. Validation

For each mirror:

- every numbered source heading and every active normative section has a
  corresponding translated heading or explicitly identified subsection;
- fenced-code blocks are balanced, and every normative command or data example
  remains technically unchanged; non-normative repeated examples may be
  consolidated;
- local Markdown links resolve;
- identifiers and command examples remain unchanged — a mirror sits beside its
  source, so relative paths need no adjustment;
- normative words such as **must**, **never**, **required**, **refused**, and
  **no write** keep the same strength;
- normative mapping and decision tables keep every source row and column;
- no translated statement contradicts a newer canonical source.

Automated structural checks help find omissions, but a human semantic review is
still required for changes to business invariants, quantity integrity,
movement immutability, permissions, migrations, backup, restore, or rollback.

## 6. Scope

Active project documentation and new operational documentation receive
Vietnamese mirrors. These are excluded by design:

- `docs/archive/`, because it contains superseded historical versions and old
  prompts;
- `docs/mockups/`, because the HTML files are executable visual references and
  their UI text is governed by `GUI_DESIGN.md`;
- `AGENTS.md` and `CLAUDE.md`, because they are AI operating instructions rather
  than user-facing project documentation. Duplicating them in another language
  would create competing instruction entry points.
