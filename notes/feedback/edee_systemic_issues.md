# EDEE Systemic Issues — Cross-Run Summary
*Source: ~30 post-mortems across 5 word problems (cereal box, mice, park, tape measure, bags)*

---

## Frequency at a glance

| # | Issue | Rough run count | Problems |
|---|-------|-----------------|----------|
| 1 | Scaffold pollution locked in by rigid img2img anchoring | 15+ | 1, 2, 3, 5 |
| 2 | Too much info: redundant labels, dividers, solution leakage | ~10 | 1, 2, 5 |
| 3 | QA rubber-stamping & severity dysfunction | nearly every failed run | all |
| 4 | EXECUTION_CONTRACT missing from inspection-side agents | 6+ | 2, 3, 5 |
| 5 | Real-world shape wrong / dimension drift | ~6 | 1, 2 |
| 6 | Measurement-instrument nonsense | 9 (every P4 run) | 4 |
| 7 | Routing misclassification (DIRECT_IMAGE_GEN) | 5 | 4 |
| 8 | Labels crowding the cropped canvas edge | 3 | 1, 3 |

## The core architectural tension (read this first)

Almost everything below traces back to one unresolved question: **what IS the scaffold?** The config currently gives three conflicting answers:

1. `design_scaffolding`: a pure mathematical skeleton, strip all flair.
2. STANDALONE RULE: a complete diagram good enough to ship on its own.
3. `plan_finishing`: a literal pixel anchor the artist must "lock exactly."

Each agent resolves the contradiction differently. The designer/coder decorate (shadows, hatching, stitching, clip-art bowls) to satisfy #2; the artist then locks that junk to satisfy #3; and `consolidate_inspection` explicitly contradicts #2 ("cosmetic blemishes the artist will cover don't matter"), so managers wave crude scaffolds through. Your own verdict resolves it: **the scaffold must stand alone as a plain-but-complete textbook diagram (accurate geometry, labels, flat solid colors) — and nothing else. Lighting, shadows, texture, anatomy, and decoration belong exclusively to the artist.** Write that one definition once, and put the same text in every stage-2 through stage-5 agent.

---

## Issue 1 — Scaffold pollution + over-rigid anchoring (the big one)

**Scaffolding symptoms:** hardcoded oval shadows under boxes/mice; hatch patterns; clip-art built from patches (Wedge cereal bowls, stitching lines, Bezier crescent moons, 4 symmetrical corner trees, "CRUNCH!" text); multi-patch legless animals; dashed decorative paths.
**Final-image symptoms:** the artist faithfully preserves all of it — CAD-looking renders, legless blob mice, ball-shaped trees, square "bags," puddle shadows, or a final image that is a pixel clone of the scaffold (zero illustrative value for the render cost).

**Agents:** `scaffolding_designer` and `coder` (inject decoration); `artist`/`plan_finishing` (locks it); `image_detail_planner` (lets decorative ideas from stage 2 harden into blueprint requirements).

**Root causes in instructions:**
- `DIRECTIVE_PRIMITIVES` "circles for cats / if in doubt fall back to circles" → Frankenstein anatomy assembled from patches.
- `DIRECTIVE_3D` "ensure depth cues (shading...) are defined" → read as "draw shadows in matplotlib."
- `plan_finishing` Directive 1–2: "lock the exact geometry... WITHOUT expanding beyond bounding boxes" makes no distinction between a measurement bracket and a placeholder ellipse — so the diffusion model can't add legs, can't fix a shadow, can't turn a rectangle into a bag.

**Fixes (general):**
- **Element taxonomy.** Require the scaffolding blueprint to tag every element as one of: `MATH-LOCKED` (measurements, counts, angles, axes, labels — artist preserves exactly), `POSITION-ANCHOR` (placeholder marks object center/scale/color only — artist redraws freely, may extend beyond the silhouette for limbs, bag contours, canopy edges), `ARTIST-ONLY` (never drawn in Python at all: shadows, lighting, textures, ground planes, decorative text, thematic scenery). Propagate the tags into `plan_finishing` so the anchor prompt is written per-class instead of "lock everything."
- **Hard bans for the coder:** no cast shadows or occlusion ovals, no hatch patterns, no pseudo-3D offset-polygon effects, no decorative text/logos, no organic subject assembled from >1–2 patches (one capsule/ellipse per animal, with orientation). Flat solid colors only.
- **Artist empowerment:** organic placeholders are mass/position anchors, not silhouettes — explicitly grant license to add anatomy, natural contours, shadows, and grounding.

---

## Issue 2 — Too much info (labels, dividers, pre-digested math)

**Symptoms:** "6 Brown Mice / 2 White Mice" badges; count captions under bags; the 1/6 cereal-box fraction pre-sliced into 6 segments (giving away the 20% → 1/5 trick); dashed dividers/fences between groups; species sorted into segregated grids instead of a natural mixed litter.

**Agents:** `markup_specialist` (invents labels to justify its turn — the prompt literally asks "what labels would this normally have?"); `layout_expert`/`arrangement_planner` (default to sorted grids); `educator` (fails as giveaway gatekeeper); `image_detail_planner` (softens rather than deletes — trimmed "Brown Mice (6)" to "Brown (6)").

**Root causes:** "don't give away the answer" is read as *don't print the final number* — nothing forbids visualizing the intermediate deduction; no distinction between marking a **given** and drawing a **derived** quantity; `merge_plans`' anti-label warning covers labels only, not dividers, boxes, or grouping.

**Fixes (general):**
- **Given-vs-derived rule** (educator, scaffolding_designer, math_check): draw only quantities *given* in the problem statement. Never pre-partition into solution fractions, unit segments, or derived equivalencies. Demarcating a stated boundary is fine; subdividing the whole into counting units is not.
- **No count labels** for drawn, distinguishable objects at N ≤ ~10 ("2 White Mice", "1 Star, 4 Moons"). Counting IS the exercise. Count labels only when items are abstracted/clumped rather than individually drawn.
- **No structural dividers** (lines, fences, boxes, panels) between groups unless the problem asks for them; visual distinction by color/position suffices.
- Add a required boolean to the math reviews: `reveals_solution_step` — flags diagrams that display the insight the student is supposed to find.

---

## Issue 3 — QA rubber-stamping & severity dysfunction

**Symptoms:** `review_aesthetics` calls ugly CAD hybrids "exceptionally clean"; verifiers pass squares-as-bags, dog-like mice, cloned scaffolds; `inspection_manager` overrules a *correct* flag (120 ticks vs 20 "improves realism") while inventing false ones; the gatekeeper passes bad art out of fear a retry loses clean text; attempt-3 leniency waves through real defects.

**Agents:** `image_verifier`, `inspector`, `inspection_manager`, `issue_aggregator`, `review_manager`.

**Root causes:** every rubric checks *absence of glitches* (blur, blank, distortion) rather than positive quality; no semantic plausibility question anywhere ("does this look like the real thing it names?"); leniency directives don't enumerate what is never waivable; vision reviewers asked to verify things vision models are bad at (counting dense ticks).

**Fixes (general):**
- Add to `review_aesthetics` / `verify_adherence`: **semantic plausibility** ("Does each named real-world object read as that object — a bag as a bag, a box with box proportions, animals with complete anatomy?") and **transformation check** ("Did the artistic pass add real illustrative value, or is this a near-copy of the scaffold?"). Require at least one concrete critique before a pass verdict.
- Give managers an explicit **never-waivable list**: wrong counts, wrong scale graduations, measurement-tool impossibilities, solution leakage, misproportioned named objects. These are structural (MAJOR+), never "cosmetic," at any attempt number. Leniency applies to style only.
- Numerical/count discrepancies must be caught in stage 3 text review (counting loop iterations in code), not delegated to vision.

---

## Issue 4 — EXECUTION_CONTRACT asymmetry (cheapest fix, do it first)

**Symptoms:** `inspection_manager` (and sometimes `inspector`/`final_reporter`) repeatedly reject or annotate correct code for omitting `ax.axis('off')` / `set_aspect('equal')` — which the wrapper handles — burning full retry loops where the coder resubmits identical code and `review_manager` has to overrule the rejection. Appeared in six-plus runs; the reviewer-side lesson was learned once but never propagated.

**Fix:** put `EXECUTION_CONTRACT` in the system identity of **every** agent that ever sees code or judges the render (`inspector`, `inspection_manager`, `final_reporter`, `error_expert`) — or fold it into `GLOBAL_TASK_EXPLANATION`. Also: (a) clarify that `set_xlim/set_ylim` are permitted (only aspect/axis-hiding is wrapper territory); (b) tell `consolidate_inspection` to judge the rendered image and the inspectors' visual reports, not to re-lint the script; (c) add margin guidance (Issue 8) to the same contract while you're in there.

---

## Issue 5 — Wrong real-world shape / dimension drift

**Symptoms:** cereal box rendered as a squat cube or thick carton, repeatedly. The pipeline had the right numbers and lost them: `dimension_expert` says "8×3×12 in" (free text) → `merge_plans` compresses to "2:3 front face" → `scaffolding_designer` picks convenient round coordinates (4×6, dx=dy=1.2) → silhouette destroyed. Reviewers verify blueprint fidelity, never real-world plausibility.

**Fixes:**
- **Structured dimensions end-to-end** (your note — generalize it): anything downstream must preserve verbatim goes in a **required schema field**, not prose. Add `aspect_ratio_wdh` (normalized W:D:H) to `estimate_dimensions`' schema and a required `dimensions` field to `merge_plans` (between `plan_response` and `final_polish`); require `scaffolding_designer`/`coder` to compute plot coordinates *from* the ratio rather than re-inventing them. Add a coder rule: never round dimensions for coordinate convenience.
- Add a **silhouette check** to `verify_adherence`: "Do proportions match the named real-world object?" — with misproportion classified as MAJOR structural, not cosmetic.

---

## Issue 6 — Measurement instruments (problem-class rules needed)

**Symptoms (all 9 P4 runs):** tape measure with 12 units labeled "20 cm"; 40 units in thirds; centimeters in eighths; ruler running past its own total; "20 cm" printed mid-tape like a logo instead of at the 0/20 overlap or an external callout.

**Root causes:** nobody in the pipeline knows how measuring tools work; `math_check` verifies the code's *internal* consistency (360/30 = 12 ✓) rather than tool semantics (12 ≠ 20); tick errors are classed as decorative; errors originate in the *blueprint* but the first math review happens after coding.

**Fixes (class rule — applies to rulers, tapes, protractors, scales, clocks, graduated cylinders, number lines, axes):**
- **Device directive** for `scaffolding_designer`/`coder`: drawn divisions MUST equal the labeled total (20 cm tape = exactly 20 major units); metric subdivides in 2/5/10 only; a tool measuring a total must show it correctly (origin at 0, reading at the endpoint/overlap) or use an external dimension callout with a leader line — never text painted mid-object.
- **`math_check` addition:** "If any scale/gauge/axis is drawn, count the divisions the code loops produce and verify they match the labeled values and standard unit subdivisions." This must be caught pre-render — vision models cannot reliably count ticks.
- **Add a `verify_blueprint` pass** (cheap text review of `scaffolding_blueprint` before `plan_logic`) so interval/proportion math errors die before they're coded. This also catches Issue 5 drift.
- Prefer a 2D cross-section/orthographic view when the measured quantity is 1D/2D (circumference, perimeter) — 3D wrapping hides where measurement starts and ends.

---

## Issue 7 — Routing misclassification

**Symptoms:** the tape-measure problem hit `DIRECT_IMAGE_GEN` in 5 of 9 runs because the subject was organic ("Python can't draw an orange"), despite explicit measurement content — the exact thing diffusion can't do.

**Root causes:** `selector`'s history scope is `STAGE3_AGENTS`, so it never sees stage 2's `requires_technical: true` or its reasoning; `choose_path` criteria weigh subject realism over measurement presence.

**Fixes:**
- Template `requires_technical`, `requires_artistic`, and `reasoning_technical` from `review_request` directly into the `choose_path` prompt; hard rule: `requires_technical: true` disallows `DIRECT_IMAGE_GEN` absent explicit justification.
- Hard trigger list: any measuring device, calibrated scale, exact count > 3, or numeric markup ⇒ `STANDARD_DIAGRAM` / `COMPOSITE_PRIMITIVES`, organic subject or not. The scaffold carries the instrument; the artist carries the orange.

---

## Issue 8 — Edge-margin crowding

**Symptoms:** labels flush against the canvas border after the wrapper's whitespace crop (3 runs). `check_overlaps` only asks "is text *cut off*?", so tight-but-inside passes.

**Fixes:** add to `EXECUTION_CONTRACT`/`write_code`: compute limits from the bounding box of **all** elements including text, then pad ~10–15% (e.g. `ax.margins(0.12)`) — "a bit larger" is not a spec. Update `check_overlaps`: flag text within ~5% of the border, not just clipped text.

---

## General rules worth writing once (cross-cutting)

1. **Structure over prose.** Any value a later stage must preserve exactly — dimensions, element anchor classes, technical flags, object counts — travels in a required schema field, never inside a paragraph. Prose is where numbers go to drift.
2. **One scaffold definition, everywhere.** Plain-but-complete standalone diagram; flat colors; math + labels only; artist owns all beauty. Delete `consolidate_inspection`'s "cosmetic blemishes don't matter" escape hatch — it contradicts the STANDALONE RULE and managers use it to wave through crude scaffolds.
3. **Catch errors at the earliest text stage.** Blueprint review before coding; count verification in code review; vision QA is a backstop for rendering, never the primary check for counts or ticks.
4. **Propagate lessons symmetrically.** When a directive fixes one agent's false positives (EXECUTION_CONTRACT), every agent judging the same artifact needs it. Asymmetric knowledge = manufactured disagreement = wasted retry loops.
5. **Positive quality bars for QA.** Every reviewer rubric should contain at least one question that a lazy pass cannot satisfy (semantic plausibility, transformation value, one mandatory concrete critique).

## Problem-class rule packs (finite categories — worth special-casing)

- **Discrete counting / probability sets** (mice, blocks, marbles): natural non-overlapping scatter or loose clusters, mixed classes — never sorted grids, panels, or dividers; no count labels; one simple blob primitive per organism with a minimum-spacing rule (≥ ~1.5× radius between centers, clear of container rims).
- **Real-world packages & containers** (boxes, bags, jars): required normalized W:D:H from a real exemplar, silhouette plausibility check; soft containers (bags, baskets) are POSITION-ANCHOR — scaffold the *contents* precisely and let the artist draw the vessel around them.
- **Measurement instruments:** the Issue 6 rules.
- **3D solids:** canonical mild viewpoints (cabinet/oblique presets, not ±30° full isometric); no scaffold shadows; per-volume face color groups ("Block B: front, top, AND side all amber") so img2img can't leak colors across faces; never 3D-ify a fundamentally 2D problem.
- **2D maps / regions** (parks, rooms, fields): a named real-world *place* always sets `requires_artistic: true` — a green rectangle is not a park; interior thematic fill is flat/top-down, elements padded off boundaries; decorative scenery is ARTIST-ONLY, never Python circles at four symmetric corners.
