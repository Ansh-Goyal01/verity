# Decisions

One line per decision (library, assumption, threshold). Newest at the bottom.

- 2026-10-27 (A0): Repo layout fixed to match CLAUDE.md. Files had been unpacked into
  `verity-starter/verity-starter/` and `verity-engine/verity-engine/`; unique files were moved to
  the root and `docs/`, three byte-identical duplicates were removed, and the empty shell folders
  were deleted with `rmdir` (which refuses non-empty folders).
- 2026-10-27 (A0): The plan PDF is stored as `docs/PLAN.pdf` (renamed from a long filename
  containing an em dash, to keep tooling safe). `docs/PLAN.md` was only a "replace me" placeholder,
  so it is kept with a pointer to the PDF rather than overwritten.
- 2026-10-27 (A0): The PDF was read with `pypdf` through an ephemeral `uv run --with pypdf
  --no-project`; pypdf is not a project dependency.
- 2026-10-27 (A0): CLAUDE.md says to use plan mode and wait for approval for multi-file changes.
  The overnight run is explicitly "never stop to ask questions", so that rule is waived for this
  run only; each milestone is instead planned in `docs/PROGRESS_ENGINE.md` and tested.
- 2026-10-27 (A0): Commits use the task file's `A<n>: <summary>` format instead of the
  conventional-commit types, and carry no co-author trailer (user rule: attribution disabled).
- 2026-10-27 (A0): `logger/`, `web/`, `design/` belong to the frontend teammate. The engine never
  creates, edits or deletes anything there; CLAUDE.md now says so and no longer mentions Vite,
  React or `npm run dev`.
