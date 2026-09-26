# Domain Docs

## Before exploring, read these

- `CONTEXT.md` at the repository root.
- `docs/adr/` entries relevant to the area being changed.

If these files do not exist, proceed silently. The domain-modeling workflows create them lazily when terminology or decisions are resolved.

## File structure

This repository uses a single-context layout:

```text
/
├── CONTEXT.md
├── docs/adr/
└── src/
```

## Use the glossary's vocabulary

Use terms as defined in `CONTEXT.md` in issue titles, proposals, hypotheses, and tests. Avoid synonyms the glossary rejects. If a needed concept is missing, reconsider the terminology or note the gap for domain modeling.

## Flag ADR conflicts

Explicitly surface output that contradicts an existing ADR rather than silently overriding it.
