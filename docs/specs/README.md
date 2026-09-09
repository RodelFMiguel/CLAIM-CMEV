# Project specifications

Status: implementation baseline derived from the [project proposal](../CLAIM-CMEV_project_proposal_v1.md). This scaffold contains specifications and reserved directories, not a running application or trained models.

The proposal governs scope. These documents define implementation contracts, acceptance criteria, and outstanding work. Choices labelled **proposed** need a recorded team decision before dependencies are pinned. Lane ownership follows proposal section 13; named members remain to be assigned.

## Start here

| Document | Purpose |
| --- | --- |
| [Product specification](product_specification.md) | Users, scope, workflow, requirements, milestones, and acceptance |
| [Technical specification](technical_specification.md) | Architecture, repository layout, deployment, model lifecycle, and data engineering |
| [Data contracts](data_contracts.md) | Shared fields, identifiers, units, versions, and invariants |
| [Application platform](application_platform.md) | Intake, API, jobs, storage, review, approval import, and exports |
| [Evaluation plan](evaluation_plan.md) | Targets, research questions, test cases, and demonstration evidence |

## Functional modules

| Module | Specification | Implementation location | Owner | Runtime |
| --- | --- | --- | --- | --- |
| M01 | [Vehicle part segmentation](module-01-vehicle-part-segmentation.md) | `src/claim_cmev/vision/parts/` | Lane 1 | Image worker |
| M02 | [Damage segmentation and part matching](module-02-damage-segmentation.md) | `src/claim_cmev/vision/damage/` | Lane 2 | Image worker |
| M03 | [Multi-view aggregation and coverage](module-03-multiview-coverage.md) | `src/claim_cmev/vision/multiview/` | Lane 2 | Image worker |
| M04 | [Document text and layout extraction](module-04-document-text-layout.md) | `src/claim_cmev/documents/text_layout/` | Lane 3 | Document worker |
| M05 | [Repair line-item recognition](module-05-repair-line-items.md) | `src/claim_cmev/documents/line_items/` | Lane 3 | Document worker |
| M06 | [Reference cost model](module-06-reference-cost-model.md) | `src/claim_cmev/costs/reference/`, `pipelines/costs/` | Lane 4 | Offline |
| M07 | [Cost anomaly detection](module-07-cost-anomaly-detection.md) | `src/claim_cmev/costs/anomaly/` | Lane 4 | Backend |
| M08 | [Evidence comparison](module-08-evidence-comparison.md) | `src/claim_cmev/comparison/` | Lane 4 | Backend |
| M09 | [Surveyor workbench and review capture](module-09-surveyor-workbench.md) | `apps/workbench/` | Lane 5 | Frontend and review API |

Lane 5 owns the application platform and contract integration. Lane 4 owns the shared vocabulary with Lanes 1-3. Lanes 1-2 share the vision training harness.

## Implementation order

1. Agree scope, contracts, taxonomy, currency, and cost units.
2. Establish API, storage, jobs, and explicitly labelled synthetic contract fixtures.
3. Connect all module boundaries and Screens 2-4 using fixtures.
4. Replace fixtures with evaluated models; identify development fixture mode explicitly.
5. Freeze evaluation inputs and versions; run the unseen-case and approval-refresh demonstrations.

The [earlier architecture sketch](../solution_architecture.md) and [problem statement](../CLAIM-CMEV_problem_statement_v4.md) are historical references. Where they differ, follow the proposal and these specifications: agreed amounts do not update reference costs directly, and absence of fraud labels does not alone establish unsupervised learning.

## Coordination tasks

- [ ] Assign named members to lanes and shared contracts.
- [ ] Review the proposed stack and record [architecture decisions](../adr/README.md).
- [ ] Resolve proposal section 16 open items through the relevant task lists.
- [ ] Agree integration fixtures and freeze contract version `0.1.0` after review.
- [ ] Record changes to scope, metrics, thresholds, and contracts with reasons.
- [ ] Review each module's acceptance evidence before declaring completion.
