# Outlines

Start from the outline for the README's role, then add the sections from
another outline that this reader needs, such as a library that ships a CLI.
Bracketed sections are optional: include them only when the evidence fills
them.

- **Project overview:** the outline for the closest kind below.
- **Component guide:** "Component of a larger repository".
- **Development or operations guide:** "Scripts, jobs, or a system to
  operate" when the reader runs it. When the reader builds or changes a
  project that has an outline of its own kind, such as an app, a library, or
  a monorepo, keep that outline in the order the work is done: setup and run
  first, then development, with what only a new user needs cut to a line.
- **Collection or catalog:** "Collection of items".

## App or service

1. Title, pitch, [badges], [screenshot or short demo of the main screen]
2. Highlights: what users can do
3. Quick start: prerequisites with versions; get the code; configure (copy the
   example environment file, name the required variables); run with one
   command; where to open it (URL, port) and what you should see
4. Configuration: table of variable, required, default, and purpose
5. Development: test, lint, and build commands; [project layout as an
   annotated tree, two levels at most]
6. [Deployment: only what the repository automates or documents]
7. [Architecture: a paragraph or small diagram when several components talk]
8. [Troubleshooting: known problems with fixes]
9. Contributing, support, license

## Library or package

1. Title, pitch, [badges: version, CI, license]
2. Highlights
3. Install: the published name with the project's package manager, or
   from-source steps when it is not published
4. Quick example: 5–15 lines that run as written, with the result
5. Usage: two to four common tasks; key options with defaults
6. [API overview: main exports, one line each, linking the full reference]
7. Compatibility: runtime versions, environments, module formats, types
8. [Upgrading: breaking changes in the current major, linking the changelog]
9. Contributing, license

## CLI tool

1. Title, pitch, [demo: terminal recording or an example with output]
2. Highlights
3. Install: each supported channel; confirm with the version flag
4. Usage: a synopsis line; three to five task-based examples (goal, command,
   output)
5. Options: table of the main flags from `--help`, linking the full help
6. [Configuration: environment variables, config files, and precedence]
7. [Exit codes: when scripts depend on them]
8. Contributing, license

## Monorepo root

1. Title, pitch for the whole system
2. [Architecture: how the parts connect, with ports]
3. Repository map: table of path, what it is, and a link to its README
4. Quick start for the whole system: prerequisites; one command to run
   everything
5. Common tasks: workspace-level commands
6. [Conventions: where new code goes, how to add a package]
7. Contributing, license

## Component of a larger repository

A folder, package, or service whose readers already work in the larger
project.

1. Title (folder or package name) and purpose; where it fits, linking the
   root README or the nearest README above it
2. [Status (stable, experimental, deprecated) and owners, when stated]
3. [What to know first: the limits and pitfalls a caller must respect]
4. Usage from the rest of the repository: how to depend on or call it, the
   import path or commands, and the directory to run them from
5. [Interface, prerequisites, and configuration, when usage alone does not
   show them]
6. Key files: a short annotated list
7. Testing this part

Link up to that README instead of repeating it. A package in a subfolder
that outside users install on its own, such as one the evidence shows is
published to a registry, takes the library, CLI, or app outline instead.

## Scripts, jobs, or a system to operate

1. Title and pitch: what it does, where it runs, and for whom
2. At a glance, whichever apply: when it runs, on which hosts or
   environments, what it produces, and the limits to know before starting
3. Before you start: access, tools with versions, and configuration. Name
   where secrets live, never their values
4. Procedures: one numbered procedure per task the files support, each with
   where to run it, the command, and the result to expect when a log or
   document records it
5. Checks: how to tell that a run worked; where logs and alerts go
6. [When something fails: only the failure modes that the scripts, logs, or
   runbooks document, each with its fix or a link to its runbook]
7. [Known gaps], [owner or contact, when stated], links to deeper docs

Warn before each destructive step.

## Collection of items

Independent items that a reader picks from and uses one at a time, such as
plugins, templates, workflows, snippets, or agent skills. Parts that run
together as one system belong to a monorepo root; material that one audience
reads or works through is a document or material collection.

1. Title, pitch for the whole collection, [badges]
2. [Lead: also what one item is and how any item is used, when this kind of
   item may be new to the reader]
3. [Highlights]
4. Get an item: the command or steps for one item, and for the whole set when
   the files support that; then one item in use
5. Catalog: one row per item in the files: its name, linked to the item's own
   document or folder; what it does or when to pick it, in a line from the
   item's own description; [status, version, or requirements, when the files
   state them and they differ]
6. [Item notes, when a row cannot show which item to pick or an item's own
   document is not written for this reader: per item, a typical use, the
   options that change how you use it, and the limit most likely to rule it
   out]
7. [Using items: what they share, such as how to run, update, and remove
   them]
8. [Add an item: where it goes, the files it needs, and how to check it, or a
   link to the contributor guide]
9. Contributing, license

A short catalog can come before "Get an item"; a long one follows it,
grouped by purpose. Name the sections with the collection's own noun. Each
item's own document keeps the rest, and a deprecated item keeps its row,
with its replacement when the files name one.

## Dataset

1. Title and description: what was measured or collected, where, when, and
   by whom
2. Key facts: coverage (ISO 8601 dates, places), size (files, rows), version,
   and the pitfalls that would skew results, such as missing-value codes,
   time zones, units, and corrections already applied
3. Files: table or tree of file, contents, format, and rows or size
4. Data dictionary: table of column, type, unit, meaning, allowed values, and
   missing-value codes
5. Methods: source and provenance, collection, processing (scripts that
   derive files and their rules), quality flags, known limitations and biases
6. Usage: a loading snippet that handles the pitfalls
7. License or terms, citation, contact

## Document or material collection

Training and course material, design assets, reports, and research material.

1. Title, purpose, and audience
2. Contents: table in the order of use: item, what it is, format, when to use
   it
3. How to use: recommended path, prerequisites, time needed when stated
4. Conventions: naming, versions and dates, where updates go
5. Access and rights: license, usage restrictions, confidentiality
6. Contact or help

Describe files you cannot open by name, type, and size only, and list their
contents as unknowns.

## Template or starter

1. Title, pitch: what a new project gets
2. Create a project: the exact command or "Use this template" flow
3. Customize: checklist of names, settings, and files to change
4. Included tooling: scripts and what each does
5. Next steps, license
