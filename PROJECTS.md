# PROJECTS.md

## Active Projects

### VIX Decomposition Research
- **Goal:** Research CBOE VIX decomposition methodology and create a public reference document
- **Status:** ✅ Complete
- **Thread:** Discord #test-run (channel 1486362952331296878)
- **Output file:** `VIX_DECOMPOSITION.md`
- **Repo:** https://github.com/wjch-bot/vix-decomposition
- **What was done:**
  - Fetched and parsed the CBOE VIX Decomposition whitepaper (Aug 2025) from `cdn.cboe.com`
  - Extracted the 6 decomposition factors: (1) sticky strike expected move, (2) parallel shift, (3) put skew gradient, (4) call skew gradient, (5) downside convexity, (6) upside convexity
  - Documented the 30-day skew interpolation formula (variance-space weighting of front/back month SPX options)
  - Included worked examples from the Yen-Carry Unwind scenario
  - Created GitHub repo `wjch-bot/vix-decomposition` and pushed the .md file

---

## Completed / Archived
_(none yet)_
