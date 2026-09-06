# CLAIM-CMEV

## Cross-Modal Evidence Verification of Declared Repair Scope Against Photographic Damage Evidence in Motor Own-Damage Claims

**Problem Statement and Motivation — Version 4, draft for team review**

**CLAIM-CMEV** denotes Cross-Modal Evidence Verification (CMEV) applied to motor claims. CLAIM is a domain prefix rather than an acronym. The name states the method and the application without asserting that the system adjudicates, which is consistent with the non-goals in §2.4.

---

## 1. The problem, in plain terms

When a car is damaged in Singapore, nobody looks up what the repair costs. One person decides.

The workshop writes a bill first. The insurer does not take it on trust, so it sends its own surveyor to the workshop to check. The surveyor walks around the car, decides which panels are damaged, decides for each one whether it can be beaten out or has to be replaced, negotiates the price down, and writes a report. The report contains a list of parts, a price against each part, and often several hundred photographs of the damage.

Everything downstream comes from that parts list: what the insurer pays, what goes into the loss data, and what the policyholder's premium looks like next year.

Two things are wrong with this arrangement.

**The surveyor spends time on the wrong half of the job.** Looking at a photograph and saying "that is a front bumper and it is dented" is the repetitive half. Judging whether a dented bumper with a radar sensor behind it can be refinished or must be replaced, and arguing the labour hours with a workshop that wants the replacement, is the skilled half. The same person does both, and the first half eats the time available for the second.

**The evidence is then thrown away.** The report is filed as a PDF. The photographs go into an archive and are never read by anything again, so nothing accumulates. The insurer knows it paid S$2,400 on that claim. It does not hold a record saying that a scuffed front bumper on a 2019 Corolla, refinished rather than replaced, costs a particular amount. That is the record that would let it recognise the next quote that is 40% above it.

Without that record, an inflated line item can only be caught by whichever surveyor happens to be standing in front of it, on that day, with that queue behind them.

Both problems have the same cause. The evidence in a motor claim is never converted into data.

---

## 2. The problem, stated precisely

### 2.1 Three cost objects, distinguished

The workflow produces three different money figures that must not be conflated:

| Symbol | Object | Produced by | Role |
|---|---|---|---|
| `C_wks` | Opening repair estimate | Workshop | Negotiating position; not the evidence under test |
| `C_est` | Declared costed estimate submitted with the survey report | Surveyor | The evidence under test |
| `C_act` | Post-adjudication approved cost | Claim handler | Realised settlement; the label |

The system interrogates `C_est` against the photographic evidence. `C_act` is the supervisory signal, available only retrospectively.

### 2.2 The task

Given a photograph set **P** (tens to hundreds of images of one damaged vehicle) and a declared scope **S** = {(part, operation, cost), …} extracted from the survey report, determine whether **S** is supported by **P**, and localise any part of **S** that is not.

Three discrepancy classes are in scope:

- **Coverage.** A part appears in the declared scope but no damage to it is observable in any photograph. Sub-cases are phantom parts, and panel spillover, where an adjacent undamaged panel is added to the scope of a genuinely damaged one.
- **Cost band.** A declared operation or price falls outside the observed distribution for that part, damage type, and vehicle class. Sub-cases are labour-hour inflation, and grade substitution, where an OEM part is billed for an aftermarket fitting.
- **Under-scoping.** Damage is visible in the photographs but absent from the declared scope. This is a repair-quality and policyholder-fairness failure rather than a fraud one, and the system should surface it.

### 2.3 Inputs and outputs

- **Inputs:** damage photograph set; survey report (scanned or PDF); vehicle attributes (make, model, year, and where available trim and ADAS fitment).
- **Intermediate representation:** a damage graph. This holds the observed damage state as a set of (part, damage type, confidence) tuples aggregated across views, aligned against the declared scope.
- **Output:** a per-line-item verdict with a reason and a visual reference, being the mask overlay on the photograph that supports it, presented to a surveyor for confirmation or override.

### 2.4 What the system does not do

- **It does not decide the claim.** It pre-fills a parts list and raises flags. A surveyor confirms, edits, and enters the agreed amount. Every output is a proposal.
- **It does not treat absence of evidence as evidence of absence.** A part declared but not visible in any supplied photograph produces a request for an additional view rather than a flag. Photographic coverage of a damaged vehicle is incomplete by default, and a system that penalises incomplete coverage would penalise honest claims with bad photography.
- **It does not detect hard fraud.** Staged collisions, phantom passengers, and policy-inception fraud fall outside the evidence this system reads.

---

## 3. Why it is worth solving

### 3.1 The sector-level symptom

Motor is the largest domestic general insurance segment in Singapore, with S$1.28 billion in gross written premiums in 2025 and a 20.9% share, and it recorded an underwriting loss of S$6.9 million that year. Net incurred claims rose 11% while recorded accident counts stayed broadly flat. The industry attributes the divergence to rising severity.

*[Figures to be cited to GIA annual statistics before submission.]*

That attribution may well be correct. EV battery packs, structural aluminium, and ADAS sensors embedded in bumpers and windscreens do raise repair costs. But it cannot currently be tested. Rising repair prices and rising claim inflation are observationally identical in aggregate loss data. They separate only at part level, and no insurer holds part-level cost history linked to damage evidence. The sector cannot diagnose its own loss ratio.

Against this, the General Insurance Association of Singapore estimates that roughly S$140 million per year is consumed paying and investigating fraudulent and inflated claims.

### 3.2 The per-claim figure

Sector aggregates support a policy paper. A product needs a per-claim figure:

> Expected leakage per inflated claim = (mean own-damage claim value) × (share of claims with inflated line items) × (mean inflation rate on those items)

**Action for the team: instantiate this from GIA data or a defensible industry proxy before the proposal is submitted.** A single sentence of the form "an inflated line item on a typical own-damage claim leaks approximately S$X, and at Y% incidence across Z claims per year this is S$N to one mid-sized insurer" carries more weight than every aggregate above it, and it is currently missing.

### 3.3 Who is worse off

| Stakeholder | Loss under the status quo |
|---|---|
| Insurer | Leakage on inflated line items; no ability to attribute loss-ratio movement to severity or to inflation |
| Policyholder | Inflation is priced into next year's premiums; under-scoped repairs return the vehicle with undetected damage |
| Honest workshop | Competes against workshops that inflate, in a market where inflation is not systematically detected |
| Surveyor | Scarce expert judgement spent on recognition work |
| Regulator / GIA | No structured basis to distinguish a severity trend from an inflation trend |

### 3.4 Legitimate divergence must be modelled

A declared cost can exceed the photographic evidence for honest reasons. The system is only defensible if these are modelled as a distinct class rather than treated as noise:

- **Hidden damage.** Structural or mechanical damage discovered on teardown that no external photograph could show.
- **Genuine severity.** ADAS recalibration, EV battery inspection protocols, and aluminium repair procedures carry real cost that the visible damage does not convey.
- **Parts price movement.** Supply shocks and model-year changes between the reference period and the claim.
- **Photographic incompleteness.** Angles, lighting, occlusion, dirt, and standing water.

A system that cannot separate these from inflation would penalise severe damage and bad photography, and any claims operation would be right to reject it.

---

## 4. Why this is tractable now

The photographs already exist. They are captured routinely and in volume, then discarded as data, so the input to an automated assessment is a by-product of a process the insurer already runs. Part segmentation and damage segmentation from vehicle imagery are established tasks with published datasets. So are multi-view aggregation and document-layout extraction from scanned reports.

The reconciliation between them does not exist: reading a costed line of text and asking whether the photographs support it. That is the contribution.

---

## 5. The reference cost baseline, stated as an assumption

The cost-band check requires a reference cost table keyed on part × damage type × vehicle class, holding a `[lo, hi]` band for each key.

**Assumption A1 (seeded baseline).** An insurer deploying CLAIM-CMEV already holds a settled-claims history from which an initial reference cost table can be derived, and CLAIM-CMEV refreshes that table continuously as confirmed surveys write back.

This is a scoping assumption, declared rather than buried. It is defensible on its own terms. An insurer that has been settling motor own-damage claims for years holds paid amounts, workshop invoices, and parts lines in its claims and finance systems. What it does not hold is that history linked to damage evidence at part level, which is the gap described in §1. Seeding the table is therefore a historical-data engineering exercise on records the insurer already owns rather than a new data-collection programme, and it is orthogonal to the modelling contribution of this project.

Four consequences of A1 must be carried through the design:

- **The seeded table is coarse and the refreshed table is fine.** Legacy records give aggregate paid amounts and inconsistent part naming, so the seed yields wide bands on a partial key set. Every confirmed survey narrows them.
- **Band width must be an explicit output.** A key seeded from twelve legacy records and a key refreshed from four hundred confirmed surveys warrant different confidence. The reconciliation engine must report the evidential strength of the band it applied, and must decline to flag against a band below a minimum support threshold.
- **The table must be versioned and time-decayed.** Parts prices move, and a band computed from 2019 records is not evidence about a 2026 claim. Records are weighted by recency and the table carries an as-of date, so any flag can be reproduced against the table version that produced it.
- **Coverage is uneven by construction.** Common panels on common vehicles get tight bands early, while low-volume models and rare damage types stay wide or unsupported for a long time. The UI should surface this rather than hide it behind a default.

**For the project deliverable.** No public dataset pairs damage images with settled repair costs (see §6.4). The team will construct a synthetic reference cost table from publicly quoted parts and labour prices for a small set of vehicle classes, document it as synthetic, and use it to demonstrate the cost-band mechanism end to end. The evaluation claim concerns the reconciliation logic: whether the engine correctly flags a line item that falls outside a given band, and correctly abstains when support is thin. It does not concern the absolute accuracy of the band values.

**Write-back still matters.** Under A1 the system is not bootstrapping from nothing, but the loop remains central. Each confirmed survey converts one claim's evidence into a structured record, and the table improves at the rate the system is used. The productivity system and the detection system remain one system.

---

## 6. Data sources

This section answers the proposal-template questions on data collection and volume. Every entry below was checked for licence terms.

### 6.1 Vision branch: part segmentation

The part-segmentation model must name panels in the same vocabulary the survey report uses. This is the panel taxonomy risk flagged in week one, and dataset choice either resolves it or entrenches it.

| Dataset | Content | Classes | Licence | Fit for CLAIM-CMEV |
|---|---|---|---|---|
| **HITL Car Parts and Car Damages** | 998 part images plus 814 damage images, polygon masks, 24,851 polygons total | 21 part classes; 8 damage classes | CC0 1.0 (public domain) | Primary. The only source found with parts and damage under the same release, at the cleanest available licence. Its 21-class part list is the taxonomy in the architecture diagram. |
| **DSMLR Car-Parts-Segmentation** (KMITL) | Multi-view images, COCO-format instance masks, plates and faces anonymised | 18 part classes, side-aware (`front_left_door`, `back_right_light`) | Research use, GitHub release | Secondary. Side-aware labels matter, because a survey report distinguishes near-side from off-side and a taxonomy that does not is unusable for reconciliation. |
| **Ultralytics Carparts-Seg** | 3,833 images, pixel masks, pre-split train/val | 23 classes (22 named plus catch-all) | AGPL-3.0 (Ultralytics) | Reserve. Convenient and pre-split, but AGPL propagates and should be read before adopting. |

The HITL dataset is dedicated to the public domain under CC0 1.0 and contains 1,812 images split into 998 part images and 814 damage images, annotated as polygons. Its part classes are windshield, back-windshield, front-window, back-window, front-door, back-door, front-wheel, back-wheel, front-bumper, back-bumper, headlight, tail-light, hood, trunk, licence-plate, mirror, roof, grille, rocker-panel, quarter-panel and fender. Its damage classes are dent, cracked, scratch, flaking, broken part, paint chip, missing part and corrosion.

**Known taxonomy shortfall.** None of these vocabularies covers panels that Singapore survey reports routinely price: A/B/C pillars, sill, wheel arch liner, radiator support, and the ADAS-bearing components such as radar brackets and camera mounts that drive the severity story in §3.1. Left-right side is present in DSMLR but absent from HITL. Dataset selection alone cannot close this gap. It requires either a mapping layer from model vocabulary to report vocabulary, or supplementary annotation, and it should be stated as a limitation.

### 6.2 Vision branch: damage segmentation

| Dataset | Content | Classes | Licence | Fit for CLAIM-CMEV |
|---|---|---|---|---|
| **CarDD** | 4,000 high-resolution images, 9,000+ instances; supports classification, detection, instance segmentation and salient object detection | 6: dent, scratch, crack, glass shatter, tire flat, lamp broken | Signed licensing form; not self-serve | Primary if access is granted. Highest annotation quality available. |
| **VehiDE** | 13,945 images, 32,000+ labelled instances | 8: broken glass, broken lights, scratch, lost parts, dents, torn, punctured, non-damaged | Kaggle mirror; original terms to verify | Primary by volume. The 8 classes are the ones in the architecture diagram. |
| **CrashCar101** | Procedurally generated synthetic images from damaged 3D car models, with pixel-accurate annotations for both parts and damage | Configurable | Academic release (WACV 2024) | High value; see below. |

Two things about CarDD matter operationally. First, its images average 684,231 pixels against roughly 50,334 pixels for the older GitHub car-damage dataset. That resolution gap has practical consequences. VehiDE's authors ran an annotator experiment and found that high-resolution images yielded more discovered damage instances, because low-resolution photographs hid small, indistinct damage from annotators entirely. Image resolution is therefore a confounder in the evidence-sufficiency logic, and a claim photographed at low resolution will systematically under-report damage. Second, CarDD does not own the copyright to its images. Access requires agreeing to Flickr and Shutterstock terms, with a signed form emailed to the authors. This is a lead-time risk and should be initiated in week one. Faces and licence plates were mosaicked or removed for privacy.

CrashCar101 warrants close attention. It uses a procedural pipeline that damages 3D car models and renders 2D images paired with pixel-accurate annotations for both part and damage categories. Models trained on real plus synthetic data outperformed real-only training for part segmentation, and the authors demonstrated sim-to-real transfer for damage segmentation. For CLAIM-CMEV it addresses three problems at once. It supplies the joint part × damage annotation that the mask-intersection step in the architecture requires and that no real dataset provides. It lets the team define its own part taxonomy rather than inherit one, which is a partial answer to §6.1. And it generates controlled multi-view renders of the same vehicle, which is the input the aggregation module needs and which §6.3 shows is otherwise unobtainable.

### 6.3 The multi-view gap

The architecture aggregates evidence across tens to hundreds of photographs of one vehicle. Every public damage dataset above is a collection of independent single images, not images grouped by vehicle. There is no public claim-level multi-view damage corpus.

Two responses are usable:

- **Synthesise the views.** CrashCar101 renders arbitrary camera positions around one damaged 3D model, giving multi-view groups with ground truth.
- **Avoid multi-view training entirely.** Van Ruitenbeek and Bhulai (*Machine Vision and Applications*, 2022) project single-view detections onto a 3D representation to merge damage across viewpoints. Evaluated on a drive-through camera rig, the approach cut duplicate damage detections by nearly 99% and false positives by 96%, using only existing single-view models and training data. Given the reflection and lighting problems in single-view vehicle inspection, this is directly relevant prior art and may be the more practical route.

Duplicate suppression matters for CLAIM-CMEV specifically. If the same dent is counted three times across three photographs, the observed damage state is wrong and every downstream reconciliation inherits the error.

### 6.4 The cost-label gap

No public dataset pairs vehicle damage images with actual repair costs. This was checked directly, and it is the reason for Assumption A1 in §5. Published work in this area either uses proprietary insurer data or manually estimates prices from parts websites, which its own authors acknowledge is unreliable. The 2025 WIREs systematic review of 55 papers on AI vehicle damage detection reaches the same conclusion about data availability being the field's binding constraint.

The team should state this plainly rather than imply that a cost model is being learned from public data.

### 6.5 Document branch: report and receipt extraction

| Dataset | Content | Task | Licence |
|---|---|---|---|
| **DocILE** | 6,680 annotated real business documents, ~100k synthetic, ~1M unlabelled for pre-training; 55 annotation classes | Key Information Localization and Extraction; Line Item Recognition | MIT |
| **CORD** | 1,000 Indonesian receipts (800/100/100), word- and line-level annotations, OCR output with boxes | 30 entities under 4 super-categories | CC BY-SA 4.0 |
| **SROIE** (ICDAR 2019) | ~1,000 scanned receipts (626 train / 347 test) | Text localisation, OCR, and KIE of company, address, date, total | MIT |
| **FUNSD** | 199 noisy scanned forms (149/50) | Entity extraction and linking: header, question, answer, other | Non-commercial academic |
| **XFUND** | FUNSD extended to 7 languages, 199 pages each | Multilingual form understanding | Non-commercial academic |
| **WildReceipt** | 1,267 train / 472 test | 25 entity types | Research |
| **RVL-CDIP** | 400,000 business documents | Document classification; pre-training corpus | Research |

DocILE is the closest public analogue to a surveyor report and should be the primary document dataset. The reason is Line Item Recognition. Business documents carry a table of invoiced goods and services where each item is a set of key information such as name, quantity and price, and LIR is the task of assigning key information to items in that table. That is structurally identical to extracting `(part, operation, cost)` triples from a costed survey estimate, and the earlier receipt benchmarks do not target it. DocILE also distinguishes KIE from KILE, where the difference is positional information. Localisation is what CLAIM-CMEV needs, because the surveyor workbench must show where on the page a flagged line item came from. The 55 annotation classes exceed earlier KIE datasets by a wide margin, the test set includes zero- and few-shot layouts, and published baselines include LayoutLMv3 and a DETR-based Table Transformer, which gives the team a starting point. It is MIT licensed and includes around 100k synthetic documents for pre-training.

CORD and SROIE remain useful as smaller, well-understood benchmarks for the OCR and field-extraction stages. CORD is CC BY-SA 4.0, which is share-alike and matters if any derived artefact is published.

**Transfer gap.** All of these are invoices, receipts, and generic forms. None is a motor survey report, and none is in a Singapore insurer's layout. The document models will be pre-trained on DocILE and fine-tuned on whatever small set of real or reconstructed survey reports the team can assemble. Layout transfer to an unseen document type is a real risk and belongs in the limitations.

### 6.6 Vehicle attributes

Stanford Cars (16,185 images, 196 make/model/year classes, roughly 50/50 split, research use only) and CompCars (around 136,700 images, non-commercial research only) both give make/model/year labels. Two cautions apply. The original Stanford Cars distribution has had availability problems, so current mirrors should be verified before the team commits to it. Both datasets are also weighted heavily toward US and Chinese market vehicles, so coverage of the Singapore fleet is questionable. If the third input branch can be satisfied from policy data rather than inferred from images, that is the better route, since the insurer already knows what car it insured.

### 6.7 Summary of the data plan

- **Part segmentation:** HITL (CC0) as the base, DSMLR for side-aware labels, CrashCar101 synthetic augmentation for taxonomy control.
- **Damage segmentation:** VehiDE for volume, CarDD for quality subject to licence approval, CrashCar101 for joint part × damage supervision.
- **Multi-view:** synthetic groups from CrashCar101, or single-view projection onto a 3D representation following van Ruitenbeek and Bhulai.
- **Document extraction:** DocILE (MIT) for line-item recognition and localisation, CORD and SROIE for OCR and field-level benchmarking, fine-tuned on a small survey-report set.
- **Reference cost table:** seeded per Assumption A1; synthetic for the project deliverable.
- **Vehicle attributes:** from policy data where possible; Stanford Cars or CompCars only as fallback.

**Volume judgement.** Combining HITL, DSMLR, VehiDE and CarDD gives roughly 20,000 real damaged-vehicle images with instance masks, augmentable via CrashCar101. That is adequate for fine-tuning pre-trained segmentation backbones and inadequate for training from scratch, which the project does not attempt. On the document side, DocILE's roughly 6,700 real and 100k synthetic documents are ample for pre-training. The binding constraint is the small number of real survey reports available for fine-tuning, and that number should be stated in the report.

---

## 7. One-line statement

> Motor claim payouts are set by one person looking at one car, and the evidence behind that decision is filed as an unstructured document and never read again. CLAIM-CMEV converts the photographs and the survey report into structured data, checks whether the declared repair scope and cost are supported by what the photographs show, and accumulates the part-level cost baseline the industry currently lacks as a by-product of doing so.

---

## 8. Open items before submission

- [ ] Initiate the CarDD licence request this week. It requires a signed form emailed to the authors and the lead time is unknown.
- [ ] Verify VehiDE's original release terms, not just the Kaggle mirror.
- [ ] Instantiate the per-claim leakage figure (§3.2).
- [ ] Cite all GIA figures to source and confirm the 2025 statistics are final.
- [ ] Build the mapping layer from the 21-class HITL part vocabulary to real survey-report panel names, and document what it cannot cover (pillars, sill, wheel arch liner, ADAS mounts).
- [ ] Decide multi-view strategy: CrashCar101 synthetic groups or single-view projection onto 3D.
- [ ] Assemble a set of real or reconstructed survey reports for document fine-tuning, and record how many.
- [ ] Confirm Ultralytics AGPL-3.0 implications before using Carparts-Seg.
- [ ] Specify the minimum support threshold below which the cost-band check abstains (§5).

---

## Sources

- Wang, Li and Wu, *CarDD: A New Dataset for Vision-Based Car Damage Detection*, IEEE T-ITS 24(7), 2023. https://cardd-ustc.github.io/
- Huynh et al., *VehiDE Dataset*, IEEE 2023; and *Powering AI-driven car damage identification based on VeHIDE dataset*, J. Information and Telecommunication 9(1), 2025
- Humans in the Loop, *Car Parts and Car Damages Dataset* (CC0 1.0). https://humansintheloop.org/resources/datasets/car-parts-and-car-damages-dataset/
- Pasupa et al., *Evaluation of deep learning algorithms for semantic segmentation of car parts*, Complex & Intelligent Systems, 2021. https://github.com/dsmlr/Car-Parts-Segmentation
- Parslov, Riise and Papadopoulos, *CrashCar101: Procedural Generation for Damage Assessment*, WACV 2024. https://crashcar.compute.dtu.dk/
- Šimsa et al., *DocILE Benchmark for Document Information Localization and Extraction*, ICDAR 2023. https://docile.rossum.ai/
- Park et al., *CORD: A Consolidated Receipt Dataset*, 2019; Huang et al., *SROIE*, ICDAR 2019; Jaume et al., *FUNSD*, 2019
- van Ruitenbeek and Bhulai, *Multi-view damage inspection using single-view damage projection*, Machine Vision and Applications 33(46), 2022
- Hasan et al., *Vehicle Damage Detection Using Artificial Intelligence: A Systematic Literature Review*, WIREs Data Mining and Knowledge Discovery, 2025
- Krause et al., *3D Object Representations for Fine-Grained Categorization* (Stanford Cars), 2013; Yang et al., *CompCars*, 2015
