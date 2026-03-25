# PROJECTS.md

## Active Projects

### VIX Decomposition Research
- **Goal:** Research + reverse-engineer exact formulas for CBOE VIX 6-factor decomposition
- **Status:** ✅ Phase 1 (methodology) + Phase 2 (implementation) complete
- **Thread:** Discord #test-run (channel 1486362952331296878)
- **Files:**
  - `VIX_DECOMPOSITION.md` — full methodology reference (cleaned formulas)
  - `vix_decomposition.py` — Python reference implementation with validation
- **Repo:** https://github.com/wjch-bot/vix-decomposition
- **What was done:**
  - Fetched/parsed CBOE VIX Decomposition whitepaper (Aug 2025) and VIX Methodology PDF
  - Reverse-engineered exact formulas for all 6 components (Eq 3–9 in doc)
  - Implemented `decompose_vix_manual()` validated against Yen-Carry Unwind ground truth
  - F1 = +2.56 pts ✓, F2 = +7.29 pts ✓ (match whitepaper exactly)
  - Built delta↔moneyness↔strike conversion for skew zone identification
  - Pushed updated .md + .py to GitHub

---

## Completed / Archived
_(none yet)_
