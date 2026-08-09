# Review: role-regression-09

## Verdict

- Overall: fail
- Qualified cases: 4 of 5
- Complete non-core coverage: 5 of 5
- Zero hard constraints: 5 of 5
- No duplicate ranking meaning: 5 of 5
- Downstream stages started: no

Case 05 uses PDF/image input formats as its core scope and emits `OCR content
from PDF and image files`, losing the explicitly named receipt domain. The
prompt currently permits a content source as scope; this is the earliest and only
remaining incorrect rule.
