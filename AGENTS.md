# Hypothesis Diversity Bench

- Preserve every attempted trial and failed attempt. Never replace invalid model responses.
- Freeze seed, cases, prompts, conditions, repeat counts and source with `bench.py plan` before inference.
- Keep rounds separate. Do not pool rounds or change a frozen round's scenarios or conditions.
- Label from the blinded review list only. Never look up the condition of an answer while reviewing it.
- Never manually edit generated summary.json, SVG figures, labels.jsonl or RESULTS.md. Rebuild with the scripts.
- Raw CLI events and credentials stay in ignored `runs/`. Public exports use explicit fields.
- Run the offline tests before changing execution, grading or analysis.
- Never use the em dash or add agent co-authors to commits.
