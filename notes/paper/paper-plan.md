# EDEE Paper — Work Plan

Advisory notes, not instructions.

> **Revised after reading the EDEE Batch Testing spreadsheet.** The earlier version of this
> plan assumed there were no current-system metrics and recommended deferring evaluation.
> That was wrong. You have a controlled evaluation with a single-call control arm, n=55 per
> condition, ~450 runs and ~$170 already spent. The paper has an evaluation; it needs
> writing up, not funding. Previous version kept at `paper-plan.md.bak`.

---

## 1. The result the paper should lead with

`2026-09-23 Gemini+Rubrics` (full pipeline) vs `2026-09-28 Gemini+Rubrics-No Agent`
(single call from the raw query), n=55 each, same rubric, same problems.

| Illustration rubric item | Full pipeline | Single-call control | Ratio |
|---|---|---|---|
| Mathematical Accuracy | 0.769 | 0.291 | 2.6× |
| Support Context Understanding | 0.615 | 0.455 | 1.4× |
| Support Context-Math Connection | 0.788 | 0.509 | 1.5× |
| Artistic Style | 0.981 | 0.927 | 1.1× |
| Simplicity | 0.865 | 0.255 | 3.4× |
| Text, Organization, Labels | 0.673 | 0.509 | 1.3× |
| **Joint (all six)** | **0.327** | **0.036** | **9.1×** |

Two-proportion test on the joint rate: **z ≈ 4.0, p < 0.0001.**

This is why the conjunctive equation (E1) belongs in the paper. Individual items improve
1.1–3.4×; the joint improves **9.1×**. An unweighted mean would have read 0.78 vs 0.49 and
badly understated the contribution. The equation is what makes the magnitude visible.

### The economics result — put this in the abstract

| | Full pipeline | Control |
|---|---|---|
| Cost per run | $0.498 | $0.140 |
| Joint success rate | 0.327 | 0.036 |
| **Cost per *usable* diagram** | **$1.52** | **$3.89** |

**2.6× cheaper per usable diagram despite costing 3.6× more per attempt.** Justifies the
architecture on economic grounds, computed from data already paid for, and nothing in this
literature reports a comparable figure.

### The two-pass decomposition, already measured

Scaffolding and illustration were scored separately, so anchor drift is directly measured:

| Condition | Scaffolding math acc. | Illustration math acc. | Drift |
|---|---|---|---|
| 09-09 Gemini | 0.982 | 0.836 | −0.146 |
| 09-10 GPT | 0.982 | 0.764 | **−0.218** |

The scaffolding pass is near-perfect on mathematics; the artistic pass is where accuracy is
lost, and GPT loses ~50% more of it than Gemini. This is simultaneously the engine
comparison, the motivation for two-layer compositing, and a quantified statement of the
paper's central tension.

---

## 2. Data problems to fix before publishing

**Scaffolding "Artistic Style" is 0.000 in every run** — all 103 scored rows in 08-17, all
55 in 09-09, everywhere. The scaffolding is *meant* to be plain, so scoring it on artistic
style is a category error, and it forces scaffolding joint satisfaction to exactly 0.000 by
construction. Drop the column and report scaffolding joint over the remaining five items,
or any published scaffolding aggregate is structurally meaningless.

**Engine and rubric comparisons are underpowered.** Gemini 0.382 vs GPT 0.345 joint, and
Gemini 0.382 vs Gemini+Rubrics 0.327 — both well inside noise at n=55 (SE ≈ 0.09). Report
descriptively, not as findings. The *completion* rates differ far more meaningfully:
Gemini 95% vs GPT 69%.

**Rubrics-in-agents may have hurt.** 09-09 → 09-23 shows joint 0.382 → 0.327 and
completion 95% → 76%. Probably noise, but it is not evidence the intervention worked, and
the to-do list has it marked done. Needs an honest sentence, not a claim.

**`NA` handling needs a stated convention.** `NA` means the run produced no artifact to
score. Quality-conditional-on-output excludes them; system success rate counts them as
failures. Both are legitimate and they differ (e.g. 09-23: 0.327 vs 0.309). Pick one as
primary, report the other, and say which is which.

---

## 3. Priority order

### P1 — Write the control comparison up

§1 is the paper. Table, joint-satisfaction equation, significance test, cost-per-usable
figure. Everything else is support. This is pure writing against data already in hand.

### P2 — The economics section

Explicitly requested in the readme, genuinely novel for an education venue, computable from
existing data. The contribution framing is already in the readme: *"fix error in code
before 1st render, 1st render before final render"* — a staged-gate argument that failures
should be caught at the cheapest stage. Pair it with the cost-per-usable result from §1.

Sources: the `Cost` and `Run Time` columns across all tabs; `session_events.json` in the
bucket carries per-event `cost` and `model` for stage-level breakdown.

### P3 — Feature-matching table

Free. The three comparison PDFs are in this folder; VISTA, MAGMA-Edu, MATH2VISUAL and
Read-Solve-Create are already in the lit review. Rows: illustrative vs plain output,
mathematical validation mechanism, model independence, human-in-the-loop, cost
transparency, grade range, open vs closed source.

### P4 — Figures

- **Joint satisfaction bar pair** (pipeline vs control) — the headline, hard to misread.
- **Scaffolding → illustration drift** per engine — shows where accuracy is lost.
- Pareto scatter of the old Table 1 only if space remains; it is now motivation, not result.

### P5 — Engine comparison expansion

The only item needing new runs, and no longer urgent: you already have Gemini vs GPT at
n=55. If the budget allows, more n on that single contrast would move it from descriptive
to inferential. Cut if the deadline is tight.

---

## 4. Equation shortlist

### E1. Joint satisfaction + Fréchet bounds — **free, already computed**

```
U_joint = P(⋂_k R_k = 1)

max(0, 1 − Σ_k (1−p_k))  ≤  P(⋂_k R_k)  ≤  min_k p_k
```

Carries the headline result. Also pre-empts "why are the absolute numbers low": six binary
criteria at 0.8 each cap joint success at 0.26, which reframes 0.327 as *above* the
independence expectation rather than as a poor score.

Worth computing the failure-clustering index too, since you have the per-run data:

```
κ = (1 − P_obs) / (1 − Π_k p_k)
```

κ < 1 means failures co-occur — which the August taxonomy says they do. Cheap, original,
and it turns the failure taxonomy into a number.

### E2. Cost per usable artifact — **free, already computed**

```
C_usable = E[c_run] / U_joint
```

$1.52 vs $3.89. The most quotable number in the paper.

### E3. Scale-invariant geometric fidelity — **free**

```
log ŝ = Σ_j w_j log(m̂_j / m_j) / Σ_j w_j
ε_j   = log( m̂_j / (ŝ · m_j) )
E_geo = sqrt( Σ_j w_j ε_j² / Σ_j w_j )
```

A textbook figure only has to be correct up to a global scale, so fit the scale first.
The one *exact* measurement available in a paper otherwise built on rater judgments,
computable from the live matplotlib `Figure` in `render-matplotlib/main.py` with no model
call. Separates "wrong proportions" from "drawn smaller," which speaks to the
perspective-distortion issue in the Fig. 2 discussion.

### E4. Anchor drift — **free, partially computed**

```
Δ_k = p_k^illustration − p_k^scaffolding
```

Already measured for Mathematical Accuracy (−0.146 Gemini, −0.218 GPT). Optionally
strengthen with the image-based version on existing scaffold/final pairs:

```
A = mean_k IoU( M_k^scaffold , M_k^final )      (anchor fidelity)
S = 1 − SSIM( scaffold , final )                (style departure)
```

### E5. Staged-gate cost identity — **free**, pairs with P2

```
c_i = Σ_μ (n_i^{in,μ}/10⁶)·r^{in}_{m,μ} + Σ_μ (n_i^{out,μ}/10⁶)·r^{out}_{m,μ} + (q_i/10³)·r^{search}_m
C   = Σ_i c_i  =  C_base + C_retry
ρ   = C_retry / C_base            (the "retry tax")
```

With `r → r'` when total tokens exceed the model's context threshold.

### E6. Verifier calibration — **cheap**, include only if space allows

```
Ā = ρ₀ + J·Q        where  J = 1 − ρ₀ − ρ₁
Q̂ = (Ā − ρ₀) / J
```

Known to education researchers as the **Rogan–Gladen prevalence correction**. Relevant
because your human ratings and the agents' own QA verdicts disagree, and you have both.
Lower priority now that human-rated outcomes exist — the agents' verdicts are no longer
load-bearing for the headline claim.

### Deferred — right idea, wrong venue

Markov repair dynamics (`Q* = α/(α+β)`), budget-constrained tier allocation, Vendi Score
for mode collapse, effective-reviewer-count (`M_eff = M/(1+(M−1)r)`). Save for a systems
venue follow-up. Four to six well-motivated equations you can compute read as rigor; ten
you can't read as padding.

---

## 5. Data inventory

### EDEE Batch Testing spreadsheet

| Tab | n | Rubric scored | Complete | $/run | min/run | Joint (illus) |
|---|---|---|---|---|---|---|
| 2026-08-17 | 110 | yes | 86% | 0.475 | 7.3 | 0.252 |
| 2026-08-24 | 55 | yes | 93% | 0.548 | 8.7 | 0.296 |
| 2026-09-09 Gemini | 55 | yes | 95% | 0.467 | 6.5 | 0.382 |
| 2026-09-10 GPT | 55 | yes | 69% | 0.423 | 7.5 | 0.345 |
| 2026-09-14 GPT+Rubrics | 57 | **no** | — | — | — | — |
| 2026-09-23 Gemini+Rubrics | 55 | yes | 76% | 0.498 | 6.8 | 0.327 |
| 2026-09-28 Gemini+Rubrics-No Agent | 55 | yes (illus only) | 100% | 0.140 | 0.4 | **0.036** |
| google img-gen | 10 | no | — | — | — | — |
| openai img-gen | 10 | no | — | — | — | — |

Rubric columns are 6 items × 2 stages (scaffolding, illustration), binary 0/1 with `NA` for
unscoreable runs, plus a computed average row at the bottom of each tab. Note the scored
rubric is **6 of the 8** items in `config.js` — Cognitive Demand and Bias were not scored.
Say so explicitly rather than implying the full rubric was applied.

Illustration joint trend: 0.252 (8/17) → 0.296 (8/24) → 0.382 (9/09). Suggestive of real
improvement, underpowered to claim it.

### Other sources

| Source | Contents | Status |
|---|---|---|
| Y3 Illustrative Mathematics sheet | 426 Grade 7 problems; 110 have a visual, 309 don't; 200 scored for image-appropriateness (85 yes / 112 no) | Link-public, read |
| GCS `edee-job-archives-0925957935` | `session_events.json` (per-event cost + model), `total_cost.json`, `scaffolding.png`, `final_illustration.png` | 360-day lifetime |
| Firestore `dee-data` | job docs incl. `user_message` condition label | TTL off, nothing expiring |
| August feedback set | ~55 runs / 11 problems, human post-mortems + ranked failure taxonomy | `notes/feedback/` |

### Numbers worth quoting

**69 of 426 Grade 7 IM problems were judged appropriate for an image and currently have
none.** That is the population the system serves, measured on a real published curriculum —
far better motivation than a generic claim about under-emphasis. A further 16 are judged
appropriate *and* already have a visual (candidates for improvement).

The corpus also serves as a **sampling frame**: drawing evaluation problems from an
externally annotated set rather than hand-picking pre-empts a cherry-picking objection.

Cleanups before quoting: normalize case in Type-of-Visual (`table`/`Table`,
`geometric figure`/`Geometric Figure`/`geometric figures`/`geomteric figure` — typo
variant), fix one `o` that should be `0` and two `NA` in the Approp column, and decide how
to report that the column is only 47% complete.

---

## 6. Editorial notes on the current draft

- **Delete "Although we do not yet have metrics for our current system."** It is false and
  it gives away the paper's strongest asset.
- **Table 1 must be relabeled as a motivating pilot.** It measures the image-to-image
  mechanic *outside* the multi-agent architecture, and its unweighted `Total` row says
  Python (0.44) beats ControlNet (0.38) — a reviewer finds that in thirty seconds. As
  motivation it's fine; as a result it's damaging. Consider cutting it entirely now that
  §1 exists, or keep it only to establish that no prior method occupies the
  accuracy-and-aesthetics region.
- **Agent/phase counts are inconsistent.** Draft says five phases and "over 20 distinct
  agents"; README says six stages; `config.js` defines 31 agents across 57 task bindings.
- The abstract currently says "we present some current examples and some metrics from a
  prior iteration." Replace with the control comparison and the cost-per-usable figure.

---

## 7. Open questions

- **Deadline and venue?** Determines whether P5 happens at all.
- Who rated the rubric columns, and was any subset double-rated? Even 20 double-rated runs
  gives you an agreement statistic, which materially strengthens every number in §1.
- Were the 55 problems in each condition the *same* 55? If so the control comparison can be
  paired (McNemar), which is more powerful than the two-proportion test and would tighten
  an already strong result.
- Do the raw per-image ratings from the old 1000-trial ControlNet study still exist?
