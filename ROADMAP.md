# Roadmap

## Summary

I use KiCad almost daily, and this is my most used software to go with it.  
LCSC is my primary supplier, and JLCPCB for prototyping, so this is a key part of my workflow.

A roadmap reflects what I personally want; a timeline reflects when something bugs me enough to actually work on it.

If I want something straight away, I will just do it, regardless of the roadmap.

I am happy to accept PRs to improve it — vibe coded or otherwise — as long as you understand every line and can fix it manually if it breaks. If you need AI to understand or fix your own submission, don't submit it.

---

## Next Up

- Sorting of the table
- Enable and disable columns

---

## Very Soon (weeks)

- Named categories

---

## Soon (months)

- Auto-categorise parts based on LCSC data and a lookup table
- Detect STEP files where X/Y/Z offsets are obviously wrong
  - Understand the root cause and fix automatically where possible
  - Reset offsets to zero where automatic correction is not possible

---

## When I Feel Like It

- Stock level checks for BOM items
- LCSC URL links pointing to the English site
- Reuse footprints and STEP models when they are identical
  - Hash STEP files to detect duplicates
  - Decide whether to trust footprint names or do deeper comparison
