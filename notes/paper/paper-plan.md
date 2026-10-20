# EDEE Paper — Work Plan

Advisory notes, not instructions. Reviewer feedback says the paper needs metrics and
equations for credibility, so this plan keeps the systems-demo framing but gives it a
quantitative backbone.

---

## 1. The framing decision (do this first — it's cheap and it unblocks everything)

The draft currently hedges between two papers:

- **(a) Systems/demo paper** — contribution is the Universal Agent framework + the
  two-pass hybrid. Evidence is architecture, economics, worked examples, feature
  comparison.
- **(b) Evaluation paper** — contribution is "our system beats baselines on an
  educator-grounded rubric." Evidence is current-system rubric scores.

You've written (a) but you're grading yourself against (b). Commit to (a) **with a
quantitative backbone** — operational metrics and derived equations rather than a
head-to-head rubric win. That satisfies the "needs metrics" feedback without funding a
1000-trial study.

### The Table 1 problem

The `Total` row is an unweighted mean of five rubric rates. Verified:

| | ControlNet | Flux | Python |
|---|---|---|---|
| Unweighted mean (current `Total`) | 0.38 | 0.34 | **0.44** |

**Python wins your own summary statistic.** A reviewer finds this in thirty seconds, and
it contradicts the sentence directly above it.

Neutral repairs don't rescue it either — unweighted geometric mean gives Python 0.356 vs
ControlNet 0.334; weighting by stated educator priorities (accuracy 0.4, aesthetics 0.2,
realism/artifacts 0.15, busyness 0.1) gives Python 0.368 vs ControlNet 0.347. The two
near-zero cells (artifacts 0.13, aesthetics 0.39) do the damage.

**Two fixes, both free:**

1. **Relabel Table 1 as a motivating pilot**, not a result. It measures the ControlNet
   mechanic *outside* the multi-agent architecture, so it was never a measurement of the
   system anyway. As motivation it's excellent: it shows no existing method occupies the
   accuracy-and-aesthetics region, which is exactly why the architecture exists.
2. **Add a joint-satisfaction row.** An illustrative diagram must be accurate *and*
   aesthetically viable; your own interviews establish accuracy as a hard gate. So the
   quantity of interest is conjunctive, not compensatory:

   | | ControlNet | Flux | Python |
   |---|---|---|---|
   | Acc ∧ Aesthetics | **0.168** | 0.041 | 0.062 |
   | Acc ∧ Realism ∧ Aesthetics | **0.049** | 0.014 | 0.018 |

   ControlNet wins by **2.7× over Python, 4.1× over Flux**. Same data, same rubric, no
   reweighting.

   Caveats to handle rather than hide: these are products of marginals (independence
   assumption). With n=1000 per cell the **raw per-image ratings give the empirical joint
   directly** — report that instead. If accuracy and aesthetics trade off *within* a
   method, the true joint is lower than the product and you need to know by how much.
   Also state the conjunctive choice as a modeling commitment grounded in the interview
   findings, not a post-hoc aggregator swap (cite Keeney & Raiffa on non-compensatory
   multi-attribute utility).

---

## 2. Priority order

### P1 — Locate and secure the results data

The readme links the *problem corpus*, not the Test Orchestrator results. Cost and timing
data lives in a different sheet plus Firestore/GCS. Find it, confirm what's still there,
and extract before planning around it.

Status: Firestore TTL is currently **off**, bucket lifetime extended to 360 days, and the
Google Sheet tracks failed runs. So nothing is actively expiring. Not urgent anymore, but
the results sheet still needs to be inventoried before the metrics section can be written.

### P2 — The economics section

The highest-value item on the list: explicitly requested, genuinely novel for an education
venue, and computable from data already captured. Almost nobody in AIED reports
$/diagram for an agentic pipeline.

The insight that makes it a *contribution* rather than a table is already in the readme:
*"fix error in code before 1st render, 1st render before final render."* That's a
staged-gate economic argument — catch failures at the cheapest stage. Show realized cost
per stage from the logs and demonstrate that cheap gates absorb most failures. It
justifies the architecture on grounds nobody else is competing on.

Data source: `session_events.json` in the bucket carries per-event `cost` and `model` for
every run. `total_cost.json` has the run total. Zero new runs needed.

### P3 — Feature-matching table

Free. The three comparison PDFs are in this folder; VISTA, MAGMA-Edu, MATH2VISUAL and
Read-Solve-Create are already in the lit review. Suggested rows: illustrative vs plain
output, mathematical validation mechanism, model independence, human-in-the-loop, cost
transparency, grade range, open vs closed source.

High reviewer value per unit effort for a demo paper. Do it while the cost data is being
extracted.

### P4 — One or two figures that earn their space

- **Pareto scatter** of Table 1 on (accuracy, aesthetics) with iso-utility curves
  `p_acc · p_aes = c`. Makes the central argument in one glance and supports the reframe
  in §1.
- **Anchor–style frontier** (see §3) if there's room for a second.

### P5 — Engine comparison (OpenAI vs Google vs local)

The only item needing new runs. Resist the 1000-trial instinct: ten problems × three
engines × one run is thirty renders and supports a perfectly publishable "preliminary
comparison" in a demo paper. Cut this entirely if the deadline is tight.

---

## 3. Equation shortlist

Chosen for credibility per dollar. Each notes its data source.

### E1. Joint-satisfaction utility + Fréchet bounds — **free**

```
U_joint = P(accurate ∧ aesthetic)

max(0, 1 − Σ_k (1−p_k))  ≤  P(∩_k R_k)  ≤  min_k p_k
```

Fixes the Table 1 problem (§1) and pre-empts "why are all the numbers low" — five binary
criteria at 0.8 each cap joint success at 0.33. That reframes 0.168 from *bad* to *4× the
practical ceiling of the alternatives*.

Source: existing 1000-trial ratings.

### E2. Verifier calibration — **cheap** (small human-labeled subset, no new generation)

```
Ā = ρ₀ + J·Q        where  J = 1 − ρ₀ − ρ₁
Q̂ = (Ā − ρ₀) / J
Var(Q̂) = Var(Ā) / J²
```

ρ₀ = false accept, ρ₁ = false reject. Keeps "our agents approved 85%" from being reported
as "85% were good." The feedback synthesis documents verifiers *praising* forbidden
shadows and paths through lakes, so ρ₀ is demonstrably not small.

Known to education researchers as the **Rogan–Gladen prevalence correction** — a
friendlier citation than the LLM-judge literature. The `1/J²` term doubles as an honest
limitations statement: a weak judge destroys your confidence interval, not just your point
estimate.

Source: the ~55-run August post-mortem set, already human-reviewed.

### E3. Scale-invariant geometric fidelity — **free**

```
log ŝ = Σ_j w_j log(m̂_j / m_j) / Σ_j w_j
ε_j   = log( m̂_j / (ŝ · m_j) )
E_geo = sqrt( Σ_j w_j ε_j² / Σ_j w_j )
```

A textbook figure only has to be correct *up to a global scale*, so fit the scale first.
Log-ratio residuals are symmetric in over/under-shoot. Counts and angles are absolute, so
they enter as a separate exact-match term.

This is the **one exact measurement** available in a paper otherwise full of LLM judgments,
and it's computable from the live matplotlib `Figure` object in `render-matplotlib/main.py`
with no model call. It also separates "wrong proportions" from "drawn smaller," which
speaks directly to the perspective-distortion issue flagged in the Fig. 2 discussion.

### E4. Anchor–style frontier — **free**

```
A = mean_k IoU( M_k^scaffold , M_k^final )      (anchor fidelity)
S = 1 − SSIM( scaffold , final )                (style departure)
```

Phase 4 is exactly this trade-off. The feedback data shows the achievable set is **bimodal
rather than tunable** ("the Anchor/Creativity dial has no middle setting" — too loose on
P4/P8/P9/P11, too tight on P10). Plotting (S, A) for the existing 55 runs turns an
anecdote into a finding, and it's the strongest motivation for two-layer compositing.

### E5. Staged-gate cost identity — **free**, pairs with P2

Per-call cost, formalizing what the cost calculator already computes:

```
c_i = Σ_μ (n_i^{in,μ}/10⁶)·r^{in}_{m,μ} + Σ_μ (n_i^{out,μ}/10⁶)·r^{out}_{m,μ} + (q_i/10³)·r^{search}_m
C   = Σ_i c_i  =  C_base + C_retry
ρ   = C_retry / C_base            (the "retry tax")
```

With the rate substitution `r → r'` when total tokens exceed the model's context
threshold. `ρ` is a number readers actually want, and it quantifies the staged-gate
argument.

### Deferred — right idea, wrong venue

Markov repair dynamics (`Q* = α/(α+β)`), budget-constrained tier allocation (knapsack over
model tiers), Vendi Score for mode collapse, effective-reviewer-count
(`M_eff = M/(1+(M−1)r)`). All defensible, all better suited to a systems venue than an
LNCS short paper. Revisit for the follow-up.

---

## 4. Data inventory

| Source | Contents | Status |
|---|---|---|
| Y3 Illustrative Mathematics sheet | 426 Grade 7 problems; 110 have a visual, 309 don't; 200 scored for image-appropriateness (85 yes / 112 no) | Link-public, read, usable |
| Test Orchestrator results sheet | per-run cost, time, images, summary | **Not yet accessible — needs sharing** |
| GCS `edee-job-archives-0925957935` | `session_events.json` (per-event cost + model), `total_cost.json`, `scaffolding.png`, `final_illustration.png` | 360-day lifetime, intact |
| Firestore `dee-data` | job docs: `original_query`, `user_message` (condition label), `status`, `generation_successful`, `human_decisions` | TTL off, nothing expiring |
| August feedback set | ~55 runs / 11 problems, human post-mortems + ranked failure taxonomy | On disk in `notes/feedback/` |
| July batch | 100 generated and reviewed | Location to confirm |

### Numbers worth quoting

From the Y3 corpus: **69 of 426 Grade 7 IM problems were judged appropriate for an image
and currently have none.** That is precisely the population the system serves, measured on
a real published curriculum — a far better motivation than a generic claim about
under-emphasis. A further 16 are judged appropriate *and* already have a visual, i.e.
candidates for improvement.

The corpus also works as a **sampling frame**: drawing evaluation problems from an
externally annotated set rather than hand-picking pre-empts a cherry-picking objection,
and costs nothing.

Cleanups needed before quoting: normalize case in Type-of-Visual
(`table`/`Table`, `geometric figure`/`Geometric Figure`/`geometric figures`/`geomteric
figure` — there's a typo variant), fix one `o` that should be `0` and two `NA` in the
Approp column, and decide how to report that the column is only 47% complete (so 85 is a
lower bound with an ambiguous denominator).

---

## 5. Editorial notes on the current draft

- **Agent/phase counts are inconsistent.** Draft says five phases and "over 20 distinct
  agents"; README says six stages; `config.js` defines 31 agents across 57 task bindings.
  Pick the true numbers and make them consistent — reviewers check.
- **"Although we do not yet have metrics for our current system"** is doing real damage in
  the Metrics section opener. Under the §1 framing it becomes "we report the system's
  operational characteristics and defer rubric evaluation to ongoing work" — same fact,
  no apology.
- Table 1's caption should state explicitly that it measures the image-to-image mechanic
  *outside* the multi-agent architecture. Right now a reader assumes it's the system.

---

## 6. Open questions

- **Deadline and venue?** If it's an AIED-style short-paper cycle, cut P5 entirely and
  spend the time on P2 and P3.
- Was the Y3 "Approp for Image?" column scored by one rater or two? Two lets you report
  agreement and strengthens the claim considerably; one is still fine if stated plainly.
- Do the raw per-image ratings from the 1000-trial study still exist? E1 is much stronger
  with empirical joints than with products of marginals.
