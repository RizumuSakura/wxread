# Domain Docs

## Layout: single-context

- `GLOSSARY.md` at the repository root defines shared domain vocabulary.
- `docs/adr/` holds architectural decision records.

## Before exploring

Read the glossary and ADRs relevant to the area being changed. If these files are absent, proceed silently; create them lazily with `domain-modeling` when terminology or decisions are actually resolved.

## Use the glossary's vocabulary

Use defined terms consistently in issue titles, proposals, hypotheses, and test names. When a needed concept is absent, reconsider whether it belongs to the domain or note the gap for `domain-modeling`.

## Flag ADR conflicts

If a proposal contradicts an existing ADR, identify the ADR and explain why its decision should be reopened instead of silently overriding it.
