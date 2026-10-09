---
name: readme-md
description: >-
  Write, improve, or review README.md files for projects, packages, folders,
  and material collections such as datasets or documents, grounded in the
  actual files. Use when asked to create, rewrite, update, polish, or critique
  a README.
---

# README

Write the README its readers need: what this is, why it matters to them, and
how to reach a first success. Ground every fact in the files.

## 1. Frame the job

Decide each item from the request and the files. Ask only when two readings
would produce different files, such as an unclear target directory.

| Item | Decide |
| --- | --- |
| Target | The directory the README will live in, normally the one it describes |
| Mode | `create` (also a from-scratch draft beside an existing README, which is then evidence), `improve` an existing README, or `review` (findings only) |
| Kind | App or service, library, CLI, monorepo root, scripts or jobs, collection of items, dataset, document or material collection, template |
| Reader | Who lands here first, what they already know, and what they came to do: user, integrator, contributor, operator, learner, or data consumer |
| Surface | Where it renders: Git host (assume one when nothing shows otherwise), package registry, internal server, or plain files |
| Role | What the README does for that reader, from the table below |
| Language | The requested language; else the existing README's; else the language of the project's docs and comments; else English |

Keep commands, identifiers, and file names verbatim in every language.

| Role | Its reader | Opens with |
| --- | --- | --- |
| Project overview | Is new to it and decides whether and how to use it | What it is, for whom, why it matters to them, and the quickest way to start |
| Component guide | Uses or changes one part of a larger system | What the part does, where it fits, and how to call it |
| Development or operations guide | Builds, deploys, runs, or recovers the system | What the work needs and the routine procedure |
| Collection or catalog | Picks one item from a set | What the set holds and how to choose and get an item |

Roles combine: a public plugin collection is a catalog inside an overview.
The reader who arrives first gets the first screen. A dataset, or material
that one audience reads or works through, is an overview of what is inside,
not a catalog.

Decide the role from the request and the evidence about readers; the path
and the repository's visibility alone do not settle it. The root README of
an internal system may be an operations guide, a subfolder may hold a
package with outside users that needs an overview, and a public README may
serve developers who need technical depth. When the role stays open, take
the one the existing README plays; with no README, take the usual one for
the place without asking: an overview at a repository root or for a folder
of data or material, a component guide for a part of a larger system.

## 2. Collect evidence

Read before you write. Inspect whichever of these exist:

- README, docs, CONTRIBUTING, CHANGELOG, SECURITY, and LICENSE. Identify the
  license from its text and report conflicts with manifest metadata.
- Manifests and lockfiles: name, published name, version, description,
  runtime versions, dependencies, entry points, and scripts. The lockfile
  decides the package manager for working on the project.
- Code that defines the interface: CLI parsers, public exports, routes,
  configuration and environment-variable reads, and defaults.
- Task runners, container files, and CI workflows: the real install, test,
  build, and run commands, plus the tested versions and platforms.
- Examples, tests, and existing images that show real usage.
- Git remote and tags, when available.
- Signs of who reads the README and where: a publish workflow or registry
  link, a private flag or internal host, the README above this one, and
  code elsewhere in the repository that uses this part.
- For collections: each item's own description, options, status, and
  examples.
- For materials: the file inventory (formats, counts, sizes, dates), the
  content of every document you can open (PDFs included), headers and
  columns, units, codes, provenance, terms of use, and the scripts that
  derive files.

Run safe, local checks that confirm usage: `--help` and version output, row
and file counts, and the project's fast test command when dependencies are
already installed. Ask before installing, using the network, or running
anything that changes files.

Keep a **fact sheet** in your working notes: each fact with its source. Add
an **unknown** for every fact a reader needs that the evidence does not
settle. An inference from file names, ordering, comments, or code you did not
run stays an unknown until a file states it or a check confirms it.

Done when every fact you plan to publish has a source, every reader-critical
gap is a listed unknown, and the reader and role from step 1 still fit the
evidence.

## 3. Find the core

- **Pitch:** one sentence under about 120 characters stating what it is, what
  it does, and for whom. Use concrete nouns and verbs; align with an accurate
  manifest description.
- **Lead:** when the pitch leaves a newcomer unsure what the project is for,
  two or three plain sentences after it, drawn from the evidence: what it is
  for and what using it looks like.
- **Highlights:** the three to five things this reader most needs to know,
  one line each, ordered by reader value. In an overview of software, give
  the strongest reasons to use it, each stated as what the reader gets and
  backed by a mechanism or fact from the evidence. In a component or
  operations guide, give the limits and pitfalls to know before using or
  running it. In a catalog, say what every item shares. For data and
  materials, say what is inside and which pitfalls would mislead a reader.
  A highlight previews a section; its details stay there. A small project
  may need none.
- **First success:** the shortest verified path from zero to a visible
  result for this reader: prerequisites, install, one command, and its
  expected output when a run or a file records it.

Done when the reader from step 1, reading only these, could decide whether
it fits and take the first step.

## 4. Outline

Give each section one question this reader has, and order the sections by
how soon the reader needs each answer. For an overview, that is an inverted
pyramid, broad to specific:

1. Title (the real name) and pitch, then the lead when step 3 calls for
   one, with optional badges and one visual.
2. Highlights.
3. Quick start.
4. Usage: common tasks and configuration.
5. Links to deeper docs: guides, API reference, and architecture.
6. Project information: status, contributing, support, and license last.

Take the outline for the role and kind from
[references/outlines.md](references/outlines.md). Include a section only when
the reader needs it and the evidence fills it; merge thin sections. Link to
deeper docs instead of copying them, and fold long optional detail into
`<details>`.

When improving, treat the existing README as the owner's design. Audit it
first: check every claim, command, link, and example against the evidence.
Treat each sample output as a copy: search the project for its title,
command, or first line to find where its producer (the code or rules that
generate it) shows the same case. Where that original differs, replace the
lines it covers with its current text, even when the producer's rules still
allow the old form, and keep the sample's other lines. Also add any label or
wrapper the producer's format requires around the output. Update any other
stale example from its current source. Then scale the edit to the request:
an update fixes what is wrong, stale, or missing; a polish also sharpens the
first screen and unclear wording; a rewrite may restructure. Outside a
rewrite, keep the owner's sections and their order, and report any
restructuring the role calls for instead of applying it. When the audit
traces stale text to breaking changes in the changelog, add a short upgrade
note that names each change existing users must act on and links the
changelog.
Keep correct, readable text and deliberate choices such as layout, badges,
emoji, alerts, credits, and headings that others link to; remove one only
when it is broken or misleading. Keep facts only the owner can know, such as
sponsors and contacts, unless the evidence contradicts them. Replace hype,
and fix or remove claims about the project that the evidence contradicts or
cannot support.

Done when each section answers a distinct reader question and nothing
essential sits below something optional.

## 5. Write

In improve mode, apply these rules to the text you add or change.

- Lead with the point. Front-load keywords in headings and first sentences.
- Say each fact once: the pitch and highlights preview it, one section holds
  it, and documents you link keep their own details instead of being copied.
- Use short sentences, one idea per paragraph, active voice, present tense,
  and "you". Write steps as imperatives.
- Choose plain words; define unavoidable jargon at first use.
- Show instead of selling: replace each adjective with the fact, example, or
  number behind it. "Tested on Python 3.10–3.12" beats "robust".
- Sound like a helpful colleague: warm, direct, and confident. Skip "simply",
  "just", and "easy"; they shame readers who struggle.
- Match tone and detail to the role: an overview orients and invites,
  showing what the reader gets and the typical case; a guide instructs,
  giving each step needed to finish the job; a catalog compares, describing
  every item in the same terms.
- Keep names exact and consistent: package, command, and file names in code
  font, and one term per concept.
- Use sentence-case headings, one H1, and no skipped levels.
- Number the steps of a sequence and bullet unordered sets. Use a table for
  options and comparisons, where readers look up or compare short values;
  give an item that needs sentences, an example, or code a list entry or its
  own subsection.
- Make code copy-paste ready: fenced with a language tag, no prompt
  characters, output in its own block, and placeholders such as
  `<api-token>` explained. Use the project's own scripts and package manager.
- Write descriptive link text. Link repository files with paths relative to
  the target directory, even when the draft is saved elsewhere; use absolute
  URLs when a package registry renders the README.
- Give every image alt text that states what it shows.
- In create mode or a rewrite, choose the opening by role. An overview for
  readers new to the project opens with emphasis: a pitch that stands out,
  then any lead, with any badges or navigation row grouped under the title,
  as [references/markdown.md](references/markdown.md) shows. A guide opens
  with a plain title and pitch and keeps its emphasis for hierarchy,
  examples, and warnings.
- Add a centered header, navigation row, badge, emoji, alert, or image only
  when it tells the reader something or gets them somewhere faster on the
  surface that shows the README.
- Add a badge only for a signal the evidence confirms, such as a CI workflow,
  a published version, or a license file; keep five or fewer, each linking to
  its source.
- Use emoji sparingly, never consecutively and never as the only signal;
  follow the project's existing tone.
- Reserve alerts such as `> [!WARNING]` for one or two crucial warnings.
- Match the repository's Markdown conventions: line wrapping, list markers,
  and lint configuration.

Size the README to what its reader must do: most small tools and folders
need 30–80 lines; libraries, apps, and datasets need 80–150; a catalog grows
with its items. Keep an example or a fact the reader's job depends on even
past these numbers. When a draft runs long, first replace reference detail
that an existing document holds with a link, then fold what remains into
`<details>`. The first screen, about 25 rendered lines, carries the pitch,
what the role opens with, and the start of the reader's first step. Add
navigation, a row of links under the pitch or a table of contents, only
past about 100 lines.

For the overview opening, badge URLs, alerts, collapsible sections,
theme-aware images, diagrams, and link and anchor rules, see
[references/markdown.md](references/markdown.md).

## 6. Verify

1. **Facts:** every name, command, flag, path, version, URL, license,
   feature, example, and sample output matches the evidence; re-run the
   cheap checks. A claim about compatibility, reproducibility, or performance
   names the conditions the evidence covers, such as the tested versions and
   platforms; how you checked it goes in your report.
2. **Examples:** build each code example from a call that the tests or
   example files exercise, when one exists; trace every input you change
   through the code, including how inputs combine. Show output only from a
   run or a file that records it, such as a test assertion; leave out output
   you worked out by hand.
3. **Unknowns:** keep them out of the README rather than guessing. Leave out
   any license, badge, install channel, URL, maintainer, roadmap, benchmark,
   or screenshot that the evidence does not support. Use placeholders only for
   values each reader supplies, such as tokens and paths.
4. **Links:** relative targets exist when resolved from the target
   directory, and in-page anchors match headings.
5. **Rendering:** fences are closed and tagged, headings are in order, and
   tables and HTML are balanced.
6. **Layout:** picture the page as its surface shows it: the headings alone
   show where each answer is, a heading or a sentence introduces each table,
   list, and code block, and table cells are short enough to compare down a
   column. In improve mode, apply this to blocks you add or change, and
   report other layout that hides an answer.
7. **First screen:** within about 25 rendered lines, the reader from step 1
   gets what the role opens with and the first command or step to take.
8. **Cut pass:** reread as that reader and delete what they would skip:
   repeated facts, hedges, sales talk, advice they did not need, and sections
   with nothing to say. In improve mode, cut only text you added or changed,
   plus stale or false content.
9. **Safety:** no secrets or credentials; use placeholders such as
   `<password>` or `example.invalid`. Include internal hostnames or personal
   contact data only when the README stays internal and its readers need
   them. Keep content marked for a narrower audience, such as answer keys or
   confidential notes, out of a README its readers can open.
10. **Improve mode:** nothing valuable dropped silently; translated READMEs
    such as `README.<lang>.md` updated or reported as stale.

## 7. Deliver

**Create or improve:** write `README.md` in the target directory, or the
file the request names, then report briefly in the conversation's language:

- What you wrote: path, role, kind, reader, and language; for a draft saved
  elsewhere, that its links are written for the target directory.
- What you verified and how, and what remains unverified.
- Unknowns as questions for the owner, such as a missing license.
- In improve mode, each substantive change with its reason, and any
  restructuring you left for a rewrite.

**Review:** change no files. Judge the README against the role and reader
from step 1; a README written for a different role is a gap. Open with a
one-line verdict, then list findings by impact: wrong or blocking
instructions first, then gaps, then style. Give each one or two lines with
its evidence and a concrete fix, and merge minor style and layout points
into one item.
