import { useState, type FormEvent } from "react";
export interface ReviewRow {
  entry_id: string;
  finding_id: string;
  description: string;
  page_id: string;
  part_code: string;
  side: string;
  operation: string;
  printed_amount: string | null;
  quantity: string | null;
  row_state: string;
  row_box_norm: number[];
}
interface Candidate {
  candidate_id: string;
  part_code: string | null;
  side: string;
  status: string;
  reason: { message: string };
}
interface Decision {
  action_type: string;
  reason_code?: string;
  note?: string;
  new_values: Record<string, unknown>;
}
export interface ReviewOverlay {
  dismissals: Record<string, Decision>;
  addition_decisions: Record<string, Decision>;
  accepted_scope: Decision[];
}
export type SubmitAction = (
  values: Record<string, unknown>,
) => Promise<boolean>;
const sides = ["unknown", "left", "right", "centre", "not_applicable"];
const operations = ["repair", "replace", "paint", "other", "unknown"];
const reasons = [
  "hidden_damage_after_dismantling",
  "adas_calibration_or_specialist_procedure",
  "parts_price_change",
  "inadequate_photograph",
  "system_error",
  "other",
];
const words = (s: string) => s.replaceAll("_", " ");
// View sentinel for a part or operation the parser could not map.
const UNMAPPED = "unmapped";
const correctionFields = [
  "part_code",
  "side",
  "operation",
  "quantity",
  "printed_line_amount",
] as const;
type CorrectionField = (typeof correctionFields)[number];
const numericFields: CorrectionField[] = ["quantity", "printed_line_amount"];
// Numbers compare by value, so "450" does not correct a displayed "450.00".
function changed(field: CorrectionField, value: string, shown: string) {
  if (numericFields.includes(field) && shown !== "")
    return Number(value) !== Number(shown);
  return value !== shown;
}
function Select({
  name,
  values,
  initial,
  title,
  required = true,
  placeholder = "Choose...",
}: {
  name: string;
  values: string[];
  initial?: string;
  title: string;
  required?: boolean;
  placeholder?: string;
}) {
  return (
    <label>
      {title}
      <select name={name} defaultValue={initial ?? ""} required={required}>
        <option value="" disabled={required}>
          {placeholder}
        </option>
        {values.map((v) => (
          <option key={v} value={v}>
            {words(v)}
          </option>
        ))}
      </select>
    </label>
  );
}
export function DismissForm({
  target,
  submit,
  locked,
}: {
  target: Record<string, unknown>;
  submit: SubmitAction;
  locked: boolean;
}) {
  return (
    <form
      className="review-controls"
      onSubmit={(e) => {
        e.preventDefault();
        const f = new FormData(e.currentTarget);
        void submit({
          ...target,
          reason_code: f.get("reason"),
          note: String(f.get("note") || "") || null,
        });
      }}
    >
      <fieldset disabled={locked}>
        <legend>Dismiss with a reason</legend>
        <Select name="reason" values={reasons} title="Dismissal reason" />
        <label>
          Dismissal note (required for other)
          <textarea name="note" maxLength={300} />
        </label>
        <button className="button button-outline button-small">
          Save dismissal
        </button>
      </fieldset>
    </form>
  );
}
export function RowControls({
  row,
  submit,
  locked,
  dismissed,
}: {
  row: ReviewRow;
  submit: SubmitAction;
  locked: boolean;
  dismissed?: Decision;
}) {
  const [unchanged, setUnchanged] = useState(false);
  // The form starts from what it displays. A view sentinel such as "unmapped"
  // is shown as an empty control, so it is never submitted as a value, and
  // only the fields the surveyor actually changed are sent.
  const shown: Record<CorrectionField, string> = {
    part_code: row.part_code === UNMAPPED ? "" : row.part_code,
    side: sides.includes(row.side) ? row.side : "",
    operation: operations.includes(row.operation) ? row.operation : "",
    quantity: row.quantity ?? "",
    printed_line_amount: row.printed_amount ?? "",
  };
  return (
    <div>
      {dismissed && (
        <p className="inline-info">
          Finding dismissed: {words(dismissed.reason_code ?? "")}.{" "}
          {dismissed.note} The original finding remains unchanged.
        </p>
      )}
      {row.row_state !== "excluded" && (
        <details className="review-controls">
          <summary>Correct row or add a missed mark</summary>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget),
                corrections: Record<string, string> = {};
              for (const field of correctionFields) {
                const value = String(f.get(field) ?? "").trim();
                if (
                  value &&
                  value !== UNMAPPED &&
                  changed(field, value, shown[field])
                )
                  corrections[field] = value;
              }
              setUnchanged(!Object.keys(corrections).length);
              if (!Object.keys(corrections).length) return;
              void submit({
                action_type: "correct_line_item",
                entry_id: row.entry_id,
                corrections,
                reason_code: f.get("reason"),
                note: String(f.get("note") || "") || null,
              });
            }}
          >
            <fieldset disabled={locked}>
              <legend>Correct extracted values</legend>
              <p>Only the values you change are saved.</p>
              <label>
                Part code
                <input
                  name="part_code"
                  defaultValue={shown.part_code}
                  placeholder={
                    row.part_code === UNMAPPED ? "Unmapped (unchanged)" : ""
                  }
                />
              </label>
              <Select
                name="side"
                values={sides}
                initial={shown.side}
                title="Side"
                required={false}
                placeholder="Unchanged"
              />
              <Select
                name="operation"
                values={operations}
                initial={shown.operation}
                title="Operation"
                required={false}
                placeholder={
                  row.operation === UNMAPPED
                    ? "Unmapped (unchanged)"
                    : "Unchanged"
                }
              />
              <label>
                Quantity
                <input
                  name="quantity"
                  type="number"
                  min="0.000001"
                  step="any"
                  defaultValue={shown.quantity}
                />
              </label>
              <label>
                Corrected printed amount
                <input
                  name="printed_line_amount"
                  type="number"
                  min="0"
                  max="999999.99"
                  step=".01"
                  defaultValue={shown.printed_line_amount}
                />
              </label>
              <Select
                name="reason"
                values={["ocr_error", "mapping_error", "wrong_row", "other"]}
                title="Correction reason"
              />
              <label>
                Correction note
                <textarea name="note" maxLength={2000} />
              </label>
              {unchanged && (
                <p className="field-error" role="alert">
                  Change at least one value before saving a correction.
                </p>
              )}
              <button className="button button-outline button-small">
                Save correction
              </button>
            </fieldset>
          </form>
          <MissedMark row={row} submit={submit} locked={locked} />
        </details>
      )}
      {!dismissed && row.row_state !== "excluded" && (
        <details className="review-controls">
          <summary>Dismiss finding</summary>
          <DismissForm
            locked={locked}
            submit={submit}
            target={{
              action_type: "dismiss_finding",
              finding_id: row.finding_id,
            }}
          />
        </details>
      )}
    </div>
  );
}
function MissedMark({
  row,
  submit,
  locked,
}: {
  row: ReviewRow;
  submit: SubmitAction;
  locked: boolean;
}) {
  const [kind, setKind] = useState("");
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        const f = new FormData(e.currentTarget);
        void submit({
          action_type: "add_mark",
          entry_id: row.entry_id,
          page_id: row.page_id,
          mark_type: kind,
          box_norm: ["x0", "y0", "x1", "y1"].map((k) => Number(f.get(k))),
          ...(kind === "price_change" ? { amount: f.get("amount") } : {}),
          note: String(f.get("note") || "") || null,
        });
      }}
    >
      <fieldset disabled={locked}>
        <legend>Add a missed mark</legend>
        <label>
          Mark type
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value)}
            required
          >
            <option value="">Choose...</option>
            <option value="exclusion">Exclusion</option>
            <option value="price_change">Price change</option>
          </select>
        </label>
        <p>
          Mark bounds on the corrected page, from 0 to 1. Fixture geometry is
          illustrative and does not align with uploaded originals.
        </p>
        <div className="coordinate-fields">
          {["x0", "y0", "x1", "y1"].map((k) => (
            <label key={k}>
              {k}
              <input
                name={k}
                type="number"
                min="0"
                max="1"
                step="any"
                required
              />
            </label>
          ))}
        </div>
        {kind === "price_change" && (
          <label>
            Revised amount
            <input
              name="amount"
              type="number"
              min="0"
              max="999999.99"
              step=".01"
              required
            />
          </label>
        )}
        <label>
          Mark note
          <textarea name="note" maxLength={2000} />
        </label>
        <button className="button button-outline button-small">
          Add confirmed mark
        </button>
      </fieldset>
    </form>
  );
}
export function CompletenessControl({
  submit,
  locked,
}: {
  submit: SubmitAction;
  locked: boolean;
}) {
  return (
    <details className="review-controls">
      <summary>Confirm declaration completeness</summary>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          void submit({
            action_type: "confirm_declaration_completeness",
            completeness_state: f.get("state"),
            reason_code: String(f.get("reason") || "") || null,
          });
        }}
      >
        <fieldset disabled={locked}>
          <Select
            name="state"
            title="Printed scope"
            values={["complete", "partial", "unreadable", "explicitly_empty"]}
          />
          <label>
            Reason code (required unless complete)
            <input name="reason" pattern="[a-z][a-z0-9_]*" />
          </label>
          <button className="button button-outline button-small">
            Confirm completeness
          </button>
        </fieldset>
      </form>
    </details>
  );
}
export function IdentityControls({
  photoIds,
  submit,
  locked,
}: {
  photoIds: string[];
  submit: SubmitAction;
  locked: boolean;
}) {
  function send(e: FormEvent<HTMLFormElement>, coverage: boolean) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    void submit({
      action_type: coverage ? "confirm_coverage" : "confirm_identity",
      part_code: f.get("part"),
      side: f.get("side"),
      photo_ids: f.getAll("photos"),
      ...(coverage
        ? {
            covers_enough: f.get("coverage") === "yes",
            reason_code: String(f.get("reason") || "") || null,
          }
        : {}),
    });
  }
  return (
    <details className="review-controls">
      <summary>Confirm physical identity or coverage</summary>
      <p>
        Fixture photo IDs refer to demonstration evidence. These confirmations
        do not establish anything about uploaded photographs.
      </p>
      {[false, true].map((coverage) => (
        <form key={String(coverage)} onSubmit={(e) => send(e, coverage)}>
          <fieldset disabled={locked}>
            <legend>
              {coverage ? "Confirm coverage" : "Confirm identity"}
            </legend>
            <label>
              Part code
              <input name="part" required />
            </label>
            <Select
              name="side"
              values={sides.filter((s) => s !== "unknown")}
              title="Physical side"
            />
            <label>
              Photographs (select one or more)
              <select name="photos" multiple required>
                {photoIds.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
            {coverage && (
              <>
                <Select
                  name="coverage"
                  values={["yes", "no"]}
                  title="Do these views show enough of this part?"
                />
                <label>
                  Reason code (required if no)
                  <input name="reason" pattern="[a-z][a-z0-9_]*" />
                </label>
              </>
            )}
            <button className="button button-outline button-small">
              {coverage ? "Save coverage" : "Save identity"}
            </button>
          </fieldset>
        </form>
      ))}
    </details>
  );
}
export function Additions({
  candidates,
  overlay,
  submit,
  locked,
}: {
  candidates: Candidate[];
  overlay: ReviewOverlay;
  submit: SubmitAction;
  locked: boolean;
}) {
  return (
    <>
      {overlay.accepted_scope.map((e, i) => (
        <p key={i}>
          Accepted by surveyor: {words(String(e.new_values.part_code))},{" "}
          {String(e.new_values.side)}, {String(e.new_values.operation)}. Amount:{" "}
          {String(
            e.new_values.amount ??
              "not supplied (" + e.new_values.amount_absent_reason + ")",
          )}
          .
        </p>
      ))}
      {!candidates.length && (
        <p className="muted">
          No possible additions were proposed. See declaration completeness and
          missing-repairs check.
        </p>
      )}
      {candidates.map((c) => {
        const decision = overlay.addition_decisions[c.candidate_id];
        return (
          <article className="line-item" key={c.candidate_id}>
            <h3>
              {words(c.part_code ?? "unresolved")} / {words(c.side)}
            </h3>
            <p>
              {words(decision?.action_type ?? c.status)}. {c.reason.message}
            </p>
            {!decision && c.status === "proposed" && (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  const f = new FormData(e.currentTarget),
                    amount = String(f.get("amount") || "");
                  void submit({
                    action_type: "accept_addition",
                    candidate_id: c.candidate_id,
                    operation: f.get("operation"),
                    quantity: f.get("quantity"),
                    amount: amount || null,
                    reason_code: String(f.get("reason") || "") || null,
                  });
                }}
              >
                <fieldset disabled={locked}>
                  <legend>Add to surveyor scope</legend>
                  <Select
                    name="operation"
                    title="Operation supplied by surveyor"
                    values={operations.filter((o) => o !== "unknown")}
                  />
                  <label>
                    Quantity supplied by surveyor
                    <input
                      name="quantity"
                      type="number"
                      min=".000001"
                      step="any"
                      required
                    />
                  </label>
                  <label>
                    Amount supplied by surveyor (optional)
                    <input
                      name="amount"
                      type="number"
                      min="0"
                      max="999999.99"
                      step=".01"
                    />
                  </label>
                  <label>
                    Reason code if amount absent
                    <input name="reason" pattern="[a-z][a-z0-9_]*" />
                  </label>
                  <button className="button button-dark button-small">
                    Accept addition
                  </button>
                </fieldset>
              </form>
            )}
            {!decision && (
              <details>
                <summary>Dismiss addition</summary>
                <DismissForm
                  submit={submit}
                  locked={locked}
                  target={{
                    action_type: "dismiss_addition",
                    candidate_id: c.candidate_id,
                  }}
                />
              </details>
            )}
          </article>
        );
      })}
    </>
  );
}
