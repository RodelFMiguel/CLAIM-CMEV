import { useEffect, useState, type FormEvent } from "react";
import {
  CheckCircle2,
  OctagonAlert,
  AlertCircle,
  Info,
  Edit3,
  Undo2,
  PenLine,
  XCircle,
  X,
} from "lucide-react";
// Frozen parts-1.0.0 vocabulary; browser regression checks against configs/taxonomy/parts.yaml.
import partOptions from "./partOptions.json";
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
export interface CoverageSlotSummary {
  part_code: string;
  side: string;
  state?: string;
  coverage?: string;
  reasons?: string[];
  reason_codes?: string[];
  coverage_confirmation_id?: string | null;
  identity_confirmation_ids?: string[];
  covering_photo_ids?: string[];
}
export type SubmitAction = (
  values: Record<string, unknown> | Record<string, unknown>[],
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
  pageNumber,
  pageLabel,
}: {
  row: ReviewRow;
  submit: SubmitAction;
  locked: boolean;
  dismissed?: Decision;
  pageNumber?: number;
  pageLabel?: string;
}) {
  const [activeModal, setActiveModal] = useState<
    "correct" | "mark" | "dismiss" | null
  >(null);

  if (row.row_state === "excluded") {
    return (
      <div className="line-item-excluded-notice">
        <span className="muted">This line item is marked as excluded.</span>
      </div>
    );
  }

  return (
    <div className="line-item-controls">
      {dismissed && (
        <div className="line-item-dismissed-banner">
          <AlertCircle size={14} className="line-item-dismissed-icon" />
          <div className="line-item-dismissed-text">
            <span>
              Finding dismissed:{" "}
              <strong>{words(dismissed.reason_code ?? "")}</strong>
              {dismissed.note ? ` — “${dismissed.note}”` : ""}.
            </span>
            <span className="line-item-dismissed-hint">
              The original extraction remains unchanged for audit compliance.
            </span>
          </div>
        </div>
      )}

      <div className="line-item-actions">
        <button
          type="button"
          className="button button-outline button-small line-item-action-btn"
          onClick={() => setActiveModal("correct")}
          disabled={locked}
          title="Correct extracted OCR values for this line item"
        >
          <Edit3 size={13} />
          <span>Correct Row</span>
        </button>

        <button
          type="button"
          className="button button-outline button-small line-item-action-btn"
          onClick={() => setActiveModal("mark")}
          disabled={locked}
          title="Add a pen mark or adjustment stroke missed by OCR"
        >
          <PenLine size={13} />
          <span>Add Missed Mark</span>
        </button>

        {!dismissed ? (
          <button
            type="button"
            className="button button-outline button-small line-item-action-btn button-dismiss-line"
            onClick={() => setActiveModal("dismiss")}
            disabled={locked}
            title="Dismiss this finding with a surveyor reason"
          >
            <XCircle size={13} />
            <span>Dismiss Finding</span>
          </button>
        ) : (
          <button
            type="button"
            className="button button-outline button-small line-item-action-btn button-dismiss-line"
            onClick={() => setActiveModal("dismiss")}
            disabled={locked}
            title="Update dismissal reason or note"
          >
            <XCircle size={13} />
            <span>Update Dismissal</span>
          </button>
        )}
      </div>

      {activeModal === "correct" && (
        <CorrectRowModal
          row={row}
          submit={submit}
          locked={locked}
          pageNumber={pageNumber}
          pageLabel={pageLabel}
          onClose={() => setActiveModal(null)}
        />
      )}

      {activeModal === "mark" && (
        <MissedMarkModal
          row={row}
          submit={submit}
          locked={locked}
          pageNumber={pageNumber}
          pageLabel={pageLabel}
          onClose={() => setActiveModal(null)}
        />
      )}

      {activeModal === "dismiss" && (
        <DismissFindingModal
          row={row}
          submit={submit}
          locked={locked}
          pageNumber={pageNumber}
          pageLabel={pageLabel}
          existingDismissal={dismissed}
          onClose={() => setActiveModal(null)}
        />
      )}
    </div>
  );
}

function CorrectRowModal({
  row,
  submit,
  locked,
  pageNumber,
  pageLabel,
  onClose,
}: {
  row: ReviewRow;
  submit: SubmitAction;
  locked: boolean;
  pageNumber?: number;
  pageLabel?: string;
  onClose: () => void;
}) {
  const [unchanged, setUnchanged] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");

  const shown: Record<CorrectionField, string> = {
    part_code: row.part_code === UNMAPPED ? "" : row.part_code,
    side: sides.includes(row.side) ? row.side : "",
    operation: operations.includes(row.operation) ? row.operation : "",
    quantity: row.quantity ?? "",
    printed_line_amount: row.printed_amount ?? "",
  };

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isSubmitting) onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isSubmitting, onClose]);

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError("");
    setUnchanged(false);

    const f = new FormData(e.currentTarget);
    const corrections: Record<string, string> = {};
    for (const field of correctionFields) {
      const value = String(f.get(field) ?? "").trim();
      if (value && value !== UNMAPPED && changed(field, value, shown[field])) {
        corrections[field] = value;
      }
    }

    if (!Object.keys(corrections).length) {
      setUnchanged(true);
      return;
    }

    setIsSubmitting(true);
    try {
      const ok = await submit({
        action_type: "correct_line_item",
        entry_id: row.entry_id,
        corrections,
        reason_code: f.get("reason"),
        note: String(f.get("note") || "") || null,
      });
      if (ok) {
        onClose();
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to save correction.",
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      className="retract-modal-overlay"
      role="dialog"
      aria-modal="true"
      onClick={(e) => {
        if (e.target === e.currentTarget && !isSubmitting) onClose();
      }}
    >
      <div className="retract-modal-card line-item-modal-card">
        <div className="retract-modal-header">
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <div className="modal-title-icon-badge modal-icon-blue">
              <Edit3 size={17} />
            </div>
            <div>
              <div className="retract-modal-title">
                Correct Line Item Extraction
              </div>
              <div className="modal-subtitle">
                <span className="modal-target-label">Item:</span>
                <span className="modal-target-value">
                  {row.description || "Line item"}
                </span>
                {pageNumber ? (
                  <span className="modal-page-badge">Page {pageNumber}</span>
                ) : null}
              </div>
            </div>
          </div>
          <button
            type="button"
            className="retract-modal-close"
            onClick={onClose}
            disabled={isSubmitting}
            aria-label="Close dialog"
          >
            <X size={16} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="quick-confirm-modal-form">
          <div className="retract-modal-body">
            <div className="modal-guidance-banner banner-info">
              <Info size={16} className="banner-icon" />
              <div className="banner-content">
                <strong>Audit rule:</strong> Only values you change are saved. Saving corrections creates a new input revision and triggers automated reassessment.
              </div>
            </div>

            {error && (
              <div className="field-error" style={{ marginBottom: "12px" }}>
                {error}
              </div>
            )}
            {unchanged && (
              <div
                className="field-error"
                role="alert"
                style={{ marginBottom: "12px" }}
              >
                Change at least one value before saving a correction.
              </div>
            )}

            <div className="quick-confirm-grid-2">
              <label className="quick-confirm-field">
                <span className="quick-confirm-label">Part Code</span>
                <input
                  name="part_code"
                  defaultValue={shown.part_code}
                  placeholder={
                    row.part_code === UNMAPPED ? "Unmapped (unchanged)" : ""
                  }
                  disabled={locked || isSubmitting}
                />
              </label>

              <label className="quick-confirm-field">
                <span className="quick-confirm-label">Side</span>
                <select
                  name="side"
                  defaultValue={shown.side}
                  disabled={locked || isSubmitting}
                >
                  <option value="">Unchanged</option>
                  {sides.map((s) => (
                    <option key={s} value={s}>
                      {words(s)}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="quick-confirm-grid-2">
              <label className="quick-confirm-field">
                <span className="quick-confirm-label">Operation</span>
                <select
                  name="operation"
                  defaultValue={shown.operation}
                  disabled={locked || isSubmitting}
                >
                  <option value="">
                    {row.operation === UNMAPPED
                      ? "Unmapped (unchanged)"
                      : "Unchanged"}
                  </option>
                  {operations.map((op) => (
                    <option key={op} value={op}>
                      {words(op)}
                    </option>
                  ))}
                </select>
              </label>

              <label className="quick-confirm-field">
                <span className="quick-confirm-label">Quantity</span>
                <input
                  name="quantity"
                  type="number"
                  min="0.000001"
                  step="any"
                  defaultValue={shown.quantity}
                  disabled={locked || isSubmitting}
                />
              </label>
            </div>

            <div className="quick-confirm-grid-2">
              <label className="quick-confirm-field">
                <span className="quick-confirm-label">
                  Corrected Printed Amount
                </span>
                <input
                  name="printed_line_amount"
                  type="number"
                  min="0"
                  max="999999.99"
                  step=".01"
                  defaultValue={shown.printed_line_amount}
                  disabled={locked || isSubmitting}
                />
              </label>

              <label className="quick-confirm-field">
                <span className="quick-confirm-label">Correction Reason</span>
                <select
                  name="reason"
                  defaultValue="ocr_error"
                  required
                  disabled={locked || isSubmitting}
                >
                  {["ocr_error", "mapping_error", "wrong_row", "other"].map(
                    (r) => (
                      <option key={r} value={r}>
                        {words(r)}
                      </option>
                    ),
                  )}
                </select>
              </label>
            </div>

            <label className="quick-confirm-field" style={{ marginTop: "4px" }}>
              <span className="quick-confirm-label">
                Correction Note <span className="label-optional">(optional)</span>
              </span>
              <textarea
                name="note"
                maxLength={2000}
                rows={3}
                placeholder="Explain the reason for modifying this extracted line item..."
                disabled={locked || isSubmitting}
              />
            </label>
          </div>

          <div className="retract-modal-actions">
            <button
              type="button"
              className="button button-outline button-modal-cancel"
              onClick={onClose}
              disabled={isSubmitting}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="button button-dark button-modal-primary"
              disabled={locked || isSubmitting}
            >
              {isSubmitting ? "Saving..." : "Save Correction"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function MissedMarkModal({
  row,
  submit,
  locked,
  pageNumber,
  pageLabel,
  onClose,
}: {
  row: ReviewRow;
  submit: SubmitAction;
  locked: boolean;
  pageNumber?: number;
  pageLabel?: string;
  onClose: () => void;
}) {
  const [kind, setKind] = useState("exclusion");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");

  const displayPage =
    pageLabel || (pageNumber ? `Estimate Page ${pageNumber}` : "the estimate page");

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isSubmitting) onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isSubmitting, onClose]);

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError("");
    const f = new FormData(e.currentTarget);

    const rawX0 = f.get("x0");
    const rawY0 = f.get("y0");
    const rawX1 = f.get("x1");
    const rawY1 = f.get("y1");

    let boxNorm: number[] | undefined = undefined;
    if (
      rawX0 !== null &&
      rawX0 !== "" &&
      rawY0 !== null &&
      rawY0 !== "" &&
      rawX1 !== null &&
      rawX1 !== "" &&
      rawY1 !== null &&
      rawY1 !== ""
    ) {
      const x0 = Number(rawX0);
      const y0 = Number(rawY0);
      const x1 = Number(rawX1);
      const y1 = Number(rawY1);

      if (
        isNaN(x0) ||
        isNaN(y0) ||
        isNaN(x1) ||
        isNaN(y1) ||
        x0 >= x1 ||
        y0 >= y1 ||
        x0 < 0 ||
        y0 < 0 ||
        x1 > 1 ||
        y1 > 1
      ) {
        setError(
          "Invalid coordinates: X0 must be less than X1, Y0 must be less than Y1, and all must be between 0.0 and 1.0.",
        );
        return;
      }
      boxNorm = [x0, y0, x1, y1];
    } else if (row.row_box_norm && row.row_box_norm.length === 4) {
      boxNorm = row.row_box_norm;
    }

    setIsSubmitting(true);
    try {
      const ok = await submit({
        action_type: "add_mark",
        entry_id: row.entry_id,
        page_id: row.page_id,
        mark_type: kind,
        ...(boxNorm ? { box_norm: boxNorm } : {}),
        ...(kind === "price_change" ? { amount: f.get("amount") } : {}),
        note: String(f.get("note") || "") || null,
      });
      if (ok) {
        onClose();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to record mark.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      className="retract-modal-overlay"
      role="dialog"
      aria-modal="true"
      onClick={(e) => {
        if (e.target === e.currentTarget && !isSubmitting) onClose();
      }}
    >
      <div className="retract-modal-card line-item-modal-card">
        <div className="retract-modal-header">
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <div className="modal-title-icon-badge modal-icon-blue">
              <PenLine size={17} />
            </div>
            <div>
              <div className="retract-modal-title">Add Missed Pen Mark</div>
              <div className="modal-subtitle">
                <span className="modal-target-label">Item:</span>
                <span className="modal-target-value">
                  {row.description || "Line item"}
                </span>
                {pageNumber ? (
                  <span className="modal-page-badge">Page {pageNumber}</span>
                ) : null}
              </div>
            </div>
          </div>
          <button
            type="button"
            className="retract-modal-close"
            onClick={onClose}
            disabled={isSubmitting}
            aria-label="Close dialog"
          >
            <X size={16} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="quick-confirm-modal-form">
          <div className="retract-modal-body">
            <div className="modal-guidance-banner banner-info">
              <Info size={16} className="banner-icon" />
              <div className="banner-content">
                <strong>Pen Mark Entry:</strong> Record an un-detected surveyor pen stroke (strike-through exclusion or handwritten price revision) on <strong>{displayPage}</strong>.
                The mark will be automatically linked to this line item on the estimate document.
              </div>
            </div>

            {error && (
              <div className="field-error" style={{ marginBottom: "12px" }}>
                {error}
              </div>
            )}

            <label
              className="quick-confirm-field"
              style={{ marginBottom: "14px" }}
            >
              <span className="quick-confirm-label">Mark Type</span>
              <select
                value={kind}
                onChange={(e) => setKind(e.target.value)}
                required
                disabled={locked || isSubmitting}
              >
                <option value="exclusion">
                  Exclusion (Strike-through / Crossed out)
                </option>
                <option value="price_change">
                  Price Change (Handwritten revised amount)
                </option>
              </select>
            </label>

            <div
              style={{
                marginBottom: "14px",
                padding: "10px 14px",
                background: "#f8fafc",
                borderRadius: "6px",
                border: "1px solid #e2e8f0",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                flexWrap: "wrap",
                gap: "8px",
              }}
            >
              <span style={{ fontSize: "12px", color: "#475569", fontWeight: 500 }}>
                Target Document Location:
              </span>
              <span
                style={{
                  fontSize: "12px",
                  color: "#0284c7",
                  fontWeight: 600,
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "5px",
                }}
              >
                <CheckCircle2 size={14} /> Auto-linked to {displayPage} row
              </span>
            </div>

            <details style={{ marginBottom: "14px", fontSize: "12px", color: "#64748b" }}>
              <summary
                style={{
                  cursor: "pointer",
                  userSelect: "none",
                  color: "#64748b",
                  fontWeight: 500,
                  fontSize: "12px",
                  padding: "4px 0",
                }}
              >
                ⚙ Custom bounding box coordinates (optional)
              </summary>
              <div className="coordinate-fields-grid" style={{ marginTop: "8px" }}>
                <div className="coordinate-card">
                  <div className="coordinate-card-label">
                    <span className="coord-name">X0</span>
                    <span className="coord-desc">Left</span>
                  </div>
                  <input
                    name="x0"
                    type="number"
                    min="0"
                    max="1"
                    step="any"
                    defaultValue={row.row_box_norm?.[0] !== undefined ? Number(row.row_box_norm[0].toFixed(3)) : ""}
                    placeholder="0.10"
                    disabled={locked || isSubmitting}
                    className="coord-input"
                  />
                </div>

                <div className="coordinate-card">
                  <div className="coordinate-card-label">
                    <span className="coord-name">Y0</span>
                    <span className="coord-desc">Top</span>
                  </div>
                  <input
                    name="y0"
                    type="number"
                    min="0"
                    max="1"
                    step="any"
                    defaultValue={row.row_box_norm?.[1] !== undefined ? Number(row.row_box_norm[1].toFixed(3)) : ""}
                    placeholder="0.20"
                    disabled={locked || isSubmitting}
                    className="coord-input"
                  />
                </div>

                <div className="coordinate-card">
                  <div className="coordinate-card-label">
                    <span className="coord-name">X1</span>
                    <span className="coord-desc">Right</span>
                  </div>
                  <input
                    name="x1"
                    type="number"
                    min="0"
                    max="1"
                    step="any"
                    defaultValue={row.row_box_norm?.[2] !== undefined ? Number(row.row_box_norm[2].toFixed(3)) : ""}
                    placeholder="0.80"
                    disabled={locked || isSubmitting}
                    className="coord-input"
                  />
                </div>

                <div className="coordinate-card">
                  <div className="coordinate-card-label">
                    <span className="coord-name">Y1</span>
                    <span className="coord-desc">Bottom</span>
                  </div>
                  <input
                    name="y1"
                    type="number"
                    min="0"
                    max="1"
                    step="any"
                    defaultValue={row.row_box_norm?.[3] !== undefined ? Number(row.row_box_norm[3].toFixed(3)) : ""}
                    placeholder="0.30"
                    disabled={locked || isSubmitting}
                    className="coord-input"
                  />
                </div>
              </div>
              <div className="coordinate-footer-tip" style={{ marginTop: "6px" }}>
                By default, the mark is automatically positioned around this line item. You only need to edit these numbers if you want to pin a custom box.
              </div>
            </details>

            {kind === "price_change" && (
              <label
                className="quick-confirm-field"
                style={{ marginBottom: "14px" }}
              >
                <span className="quick-confirm-label">
                  Revised Surveyor Amount
                </span>
                <input
                  name="amount"
                  type="number"
                  min="0"
                  max="999999.99"
                  step=".01"
                  required
                  placeholder="0.00"
                  disabled={locked || isSubmitting}
                />
              </label>
            )}

            <label className="quick-confirm-field">
              <span className="quick-confirm-label">
                Mark Note <span className="label-optional">(optional)</span>
              </span>
              <textarea
                name="note"
                maxLength={2000}
                rows={3}
                placeholder="Optional explanation of the pen mark or surveyor intention..."
                disabled={locked || isSubmitting}
              />
            </label>
          </div>

          <div className="retract-modal-actions">
            <button
              type="button"
              className="button button-outline button-modal-cancel"
              onClick={onClose}
              disabled={isSubmitting}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="button button-dark button-modal-primary"
              disabled={locked || isSubmitting}
            >
              {isSubmitting ? "Adding..." : "Add Confirmed Mark"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function DismissFindingModal({
  row,
  submit,
  locked,
  pageNumber,
  pageLabel,
  existingDismissal,
  onClose,
}: {
  row: ReviewRow;
  submit: SubmitAction;
  locked: boolean;
  pageNumber?: number;
  pageLabel?: string;
  existingDismissal?: Decision;
  onClose: () => void;
}) {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isSubmitting) onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isSubmitting, onClose]);

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError("");
    const f = new FormData(e.currentTarget);
    const reason = String(f.get("reason") || "");
    const note = String(f.get("note") || "").trim();

    if (reason === "other" && !note) {
      setError(
        "Please provide a note explaining the reason when selecting 'Other'.",
      );
      return;
    }

    setIsSubmitting(true);
    try {
      const ok = await submit({
        action_type: "dismiss_finding",
        finding_id: row.finding_id,
        reason_code: reason,
        note: note || null,
      });
      if (ok) {
        onClose();
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to dismiss finding.",
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      className="retract-modal-overlay"
      role="dialog"
      aria-modal="true"
      onClick={(e) => {
        if (e.target === e.currentTarget && !isSubmitting) onClose();
      }}
    >
      <div className="retract-modal-card line-item-modal-card">
        <div className="retract-modal-header">
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <div className="modal-title-icon-badge modal-icon-red">
              <XCircle size={17} />
            </div>
            <div>
              <div className="retract-modal-title">Dismiss Finding</div>
              <div className="modal-subtitle">
                <span className="modal-target-label">Item:</span>
                <span className="modal-target-value">
                  {row.description || "Line item"}
                </span>
                {pageNumber ? (
                  <span className="modal-page-badge">Page {pageNumber}</span>
                ) : null}
              </div>
            </div>
          </div>
          <button
            type="button"
            className="retract-modal-close"
            onClick={onClose}
            disabled={isSubmitting}
            aria-label="Close dialog"
          >
            <X size={16} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="quick-confirm-modal-form">
          <div className="retract-modal-body">
            <div className="modal-guidance-banner banner-danger">
              <AlertCircle size={16} className="banner-icon" />
              <div className="banner-content">
                <strong>Surveyor Decision:</strong> Dismissing marks this finding as disregarded in the review overlay. The original extracted line item remains preserved in the immutable audit log.
              </div>
            </div>

            {error && (
              <div className="field-error" style={{ marginBottom: "12px" }}>
                {error}
              </div>
            )}

            <label
              className="quick-confirm-field"
              style={{ marginBottom: "14px" }}
            >
              <span className="quick-confirm-label">Dismissal Reason</span>
              <select
                name="reason"
                defaultValue={
                  existingDismissal?.reason_code ?? "inadequate_photograph"
                }
                required
                disabled={locked || isSubmitting}
              >
                {reasons.map((r) => (
                  <option key={r} value={r}>
                    {words(r)}
                  </option>
                ))}
              </select>
            </label>

            <label className="quick-confirm-field">
              <span className="quick-confirm-label">
                Dismissal Note <span className="label-optional">(required if &ldquo;Other&rdquo;)</span>
              </span>
              <textarea
                name="note"
                maxLength={300}
                rows={3}
                defaultValue={existingDismissal?.note ?? ""}
                placeholder="Explain why this finding is being dismissed..."
                disabled={locked || isSubmitting}
              />
            </label>
          </div>

          <div className="retract-modal-actions">
            <button
              type="button"
              className="button button-outline button-modal-cancel"
              onClick={onClose}
              disabled={isSubmitting}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="button button-danger button-modal-danger"
              disabled={locked || isSubmitting}
            >
              {isSubmitting ? "Dismissing..." : "Dismiss Finding"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
export interface DeclarationData {
  state: string;
  source: string;
  reasons?: string[];
  confirmed_by?: string | null;
  confirmed_at?: string | null;
  review_revision?: number | null;
  layout_family?: string | null;
  unparsed_region_count?: number;
}

const REASON_PRESETS: Record<string, { code: string; label: string }[]> = {
  partial: [
    { code: "missing_pages", label: "Missing one or more estimate pages" },
    { code: "table_truncated", label: "Repair table cut off at bottom of page" },
    { code: "continuation_missing", label: "Continuation page missing header" },
    { code: "subtotal_mismatch", label: "Subtotal or item sum mismatch" },
    { code: "unparsed_table_text", label: "Unparsed table regions" },
    { code: "custom", label: "Other / custom reason code..." },
  ],
  unreadable: [
    { code: "blurry_scan", label: "Blurry or illegible scan" },
    { code: "unsupported_layout", label: "Unsupported document layout" },
    { code: "damaged_page", label: "Damaged or corrupted page" },
    { code: "custom", label: "Other / custom reason code..." },
  ],
  explicitly_empty: [
    { code: "surveyor_confirmed_empty", label: "Surveyor confirmed empty scope" },
    { code: "total_loss_inspection", label: "Total loss inspection (no repairs)" },
    { code: "custom", label: "Other / custom reason code..." },
  ],
};

function formatStateLabel(st: string): string {
  switch (st) {
    case "complete":
      return "Complete Scope";
    case "partial":
      return "Partial Scope";
    case "unreadable":
      return "Unreadable Document";
    case "explicitly_empty":
      return "Explicitly Empty";
    default:
      return words(st);
  }
}

export function CompletenessControl({
  declaration,
  completenessState,
  submit,
  locked,
}: {
  declaration?: DeclarationData | null;
  completenessState?: string;
  submit: SubmitAction;
  locked: boolean;
}) {
  const currentState = declaration?.state || completenessState || "complete";
  const isConfirmed = declaration?.source === "human_confirmation";

  const [open, setOpen] = useState(false);
  const [selectedState, setSelectedState] = useState(currentState);
  const [selectedReason, setSelectedReason] = useState("");
  const [customReason, setCustomReason] = useState("");
  const [note, setNote] = useState("");
  const [formError, setFormError] = useState("");

  useEffect(() => {
    setSelectedState(currentState);
  }, [currentState]);

  const isIdenticalConfirmed = isConfirmed && selectedState === currentState;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError("");

    if (isIdenticalConfirmed) {
      setFormError(
        `Declaration completeness is already confirmed as ${currentState}. Select a different state to amend.`,
      );
      return;
    }

    let finalReason: string | null = null;
    if (selectedState !== "complete") {
      finalReason =
        selectedReason === "custom"
          ? customReason.trim().toLowerCase()
          : selectedReason;
      if (!finalReason) {
        setFormError(
          "A reason code is required for partial, unreadable, or explicitly empty scopes.",
        );
        return;
      }
      if (!/^[a-z][a-z0-9_]*$/.test(finalReason)) {
        setFormError(
          "Reason code must be a lower-case identifier (e.g. missing_page_2).",
        );
        return;
      }
    }

    const payload: Record<string, unknown> = {
      action_type: "confirm_declaration_completeness",
      completeness_state: selectedState,
      reason_code: finalReason,
      note: note.trim() || null,
    };

    const ok = await submit(payload);
    if (ok) {
      setOpen(false);
      setNote("");
      setSelectedReason("");
      setCustomReason("");
    }
  }

  return (
    <div className="completeness-container">
      <div className="completeness-status-bar">
        <div className="completeness-status-left">
          <span className={`completeness-badge completeness-badge-${currentState}`}>
            {currentState === "complete" && <CheckCircle2 size={14} />}
            {currentState === "partial" && <OctagonAlert size={14} />}
            {currentState === "unreadable" && <AlertCircle size={14} />}
            {currentState === "explicitly_empty" && <Info size={14} />}
            {formatStateLabel(currentState)}
          </span>
          <div className="completeness-status-text">
            {isConfirmed ? (
              <span>
                <strong>Confirmed by {declaration?.confirmed_by || "Surveyor"}</strong>
                {declaration?.confirmed_at && (
                  <> &middot; {new Date(declaration.confirmed_at).toLocaleDateString()}</>
                )}
                {declaration?.review_revision !== undefined &&
                  declaration?.review_revision !== null && (
                    <span className="completeness-rev-tag">
                      Review Rev {declaration.review_revision}
                    </span>
                  )}
              </span>
            ) : (
              <span>
                <strong>Machine detected ({declaration?.source || "parser"})</strong>
                {" "}&middot; Awaiting surveyor confirmation
              </span>
            )}
            {declaration?.reasons && declaration.reasons.length > 0 && (
              <span className="completeness-reasons-preview">
                Reasons: {declaration.reasons.join(", ")}
              </span>
            )}
          </div>
        </div>
        <button
          type="button"
          className={`button button-small ${isConfirmed ? "button-outline" : "button-dark"}`}
          onClick={() => {
            setOpen(!open);
            setFormError("");
          }}
          disabled={locked}
          style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
        >
          {open ? (
            "Close"
          ) : isConfirmed ? (
            <>
              <Edit3 size={13} /> Amend Status
            </>
          ) : (
            <>
              <CheckCircle2 size={13} /> Confirm Completeness
            </>
          )}
        </button>
      </div>

      {open && (
        <form className="completeness-amendment-card" onSubmit={handleSubmit}>
          <div className="completeness-amendment-header">
            <h3>
              {isConfirmed
                ? "Amend Declaration Completeness"
                : "Confirm Declaration Completeness"}
            </h3>
            <p>
              Rule A1 gates missing repair additions on estimate completeness. If
              the printed estimate was misdeclared, choose the corrected state
              below.
            </p>
          </div>

          {formError && (
            <div className="field-error-banner" role="alert">
              <Info size={16} />
              <span>{formError}</span>
            </div>
          )}

          <fieldset
            disabled={locked}
            style={{
              border: "none",
              padding: 0,
              margin: 0,
              display: "flex",
              flexDirection: "column",
              gap: "14px",
            }}
          >
            <div className="completeness-options-grid">
              <label
                className={`confirmation-option ${selectedState === "complete" ? "is-selected" : ""}`}
              >
                <input
                  type="radio"
                  name="completeness_state"
                  value="complete"
                  checked={selectedState === "complete"}
                  onChange={() => {
                    setSelectedState("complete");
                    setFormError("");
                  }}
                />
                <div className="confirmation-option-body">
                  <div className="confirmation-option-title">Complete Scope</div>
                  <div className="confirmation-option-desc">
                    All estimate pages and repair line items are present and
                    readable. Unlocks missing-repairs detection (Rule A1).
                  </div>
                </div>
              </label>

              <label
                className={`confirmation-option ${selectedState === "partial" ? "is-selected" : ""}`}
              >
                <input
                  type="radio"
                  name="completeness_state"
                  value="partial"
                  checked={selectedState === "partial"}
                  onChange={() => {
                    setSelectedState("partial");
                    setFormError("");
                  }}
                />
                <div className="confirmation-option-body">
                  <div className="confirmation-option-title">Partial Scope</div>
                  <div className="confirmation-option-desc">
                    One or more pages or table rows are missing or cut off.
                    Missing repairs will be withheld.
                  </div>
                </div>
              </label>

              <label
                className={`confirmation-option ${selectedState === "unreadable" ? "is-selected" : ""}`}
              >
                <input
                  type="radio"
                  name="completeness_state"
                  value="unreadable"
                  checked={selectedState === "unreadable"}
                  onChange={() => {
                    setSelectedState("unreadable");
                    setFormError("");
                  }}
                />
                <div className="confirmation-option-body">
                  <div className="confirmation-option-title">
                    Unreadable Document
                  </div>
                  <div className="confirmation-option-desc">
                    The scan or pages cannot be deciphered. Missing repairs will
                    be withheld.
                  </div>
                </div>
              </label>

              <label
                className={`confirmation-option ${selectedState === "explicitly_empty" ? "is-selected" : ""}`}
              >
                <input
                  type="radio"
                  name="completeness_state"
                  value="explicitly_empty"
                  checked={selectedState === "explicitly_empty"}
                  onChange={() => {
                    setSelectedState("explicitly_empty");
                    setFormError("");
                  }}
                />
                <div className="confirmation-option-body">
                  <div className="confirmation-option-title">
                    Explicitly Empty Scope
                  </div>
                  <div className="confirmation-option-desc">
                    Genuine total loss or zero repair rows claimed. Requires zero
                    declared rows.
                  </div>
                </div>
              </label>
            </div>

            {selectedState !== "complete" ? (
              <div className="completeness-reason-box">
                <label
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: "6px",
                  }}
                >
                  <span
                    style={{
                      fontWeight: 600,
                      fontSize: "12px",
                      color: "#1e293b",
                    }}
                  >
                    Reason for {formatStateLabel(selectedState)} (Required)
                  </span>
                  <select
                    value={selectedReason}
                    onChange={(e) => setSelectedReason(e.target.value)}
                    required
                  >
                    <option value="" disabled>
                      Select reason...
                    </option>
                    {(REASON_PRESETS[selectedState] || []).map((preset) => (
                      <option key={preset.code} value={preset.code}>
                        {preset.label} ({preset.code})
                      </option>
                    ))}
                  </select>
                </label>

                {selectedReason === "custom" && (
                  <label
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: "6px",
                    }}
                  >
                    <span
                      style={{
                        fontWeight: 600,
                        fontSize: "12px",
                        color: "#1e293b",
                      }}
                    >
                      Custom Reason Code (lowercase letters and underscores):
                    </span>
                    <input
                      type="text"
                      value={customReason}
                      onChange={(e) => setCustomReason(e.target.value)}
                      pattern="[a-z][a-z0-9_]*"
                      placeholder="e.g. missing_page_2"
                      required
                    />
                  </label>
                )}
              </div>
            ) : (
              <div className="inline-info" style={{ margin: 0 }}>
                <Info size={16} />
                <span>
                  Confirming as Complete unlocks Rule A1. The system will evaluate
                  all physical damage observations and propose any unquoted
                  repair additions.
                </span>
              </div>
            )}

            {isIdenticalConfirmed && (
              <div
                className="field-error-banner"
                role="alert"
                style={{ margin: 0 }}
              >
                <Info size={16} />
                <span>
                  Declaration completeness is already confirmed as {currentState}.
                  Select a different state (e.g.{" "}
                  {currentState === "complete" ? "Partial" : "Complete"}) to
                  amend.
                </span>
              </div>
            )}

            <label
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "6px",
              }}
            >
              <span
                style={{
                  fontWeight: 600,
                  fontSize: "12px",
                  color: "#1e293b",
                }}
              >
                Surveyor Note (Optional)
              </span>
              <textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Add context for this declaration determination..."
                rows={2}
                maxLength={300}
              />
            </label>

            <div className="completeness-actions">
              <button
                type="button"
                className="button button-outline button-small"
                onClick={() => setOpen(false)}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="button button-dark button-small"
                disabled={locked || isIdenticalConfirmed}
              >
                {isConfirmed
                  ? `Save Amendment (Set to ${words(selectedState)})`
                  : `Confirm Scope as ${words(selectedState)}`}
              </button>
            </div>
          </fieldset>
        </form>
      )}
    </div>
  );
}
function UnifiedIdentityCoverageForm({
  photos,
  submit,
  locked,
  existingCoverage = [],
}: {
  photos: { id: string; label: string }[];
  submit: SubmitAction;
  locked: boolean;
  existingCoverage?: CoverageSlotSummary[];
}) {
  const [part, setPart] = useState("");
  const [side, setSide] = useState("");
  const [confirmIdentity, setConfirmIdentity] = useState(true);
  const [evaluateCoverage, setEvaluateCoverage] = useState(true);
  const [coverageState, setCoverageState] = useState<"adequate" | "inadequate">("adequate");
  const [insufficientReason, setInsufficientReason] = useState("");
  const [formError, setFormError] = useState("");

  const selectedPart = partOptions.parts.find((p) => p.code === part);
  const matchingSlot = existingCoverage.find(
    (c) => c.part_code === part && c.side === side,
  );
  const slotState = matchingSlot?.state ?? matchingSlot?.coverage ?? "unresolved";
  const isSlotRetracted =
    matchingSlot?.reasons?.includes("surveyor_retracted") ||
    matchingSlot?.reason_codes?.includes("surveyor_retracted");
  const hasConfirmedCoverage =
    matchingSlot &&
    (slotState === "adequate" ||
      Boolean(matchingSlot.coverage_confirmation_id) ||
      Boolean(
        matchingSlot.identity_confirmation_ids &&
          matchingSlot.identity_confirmation_ids.length > 0,
      ));

  function handlePartChange(newPart: string) {
    setPart(newPart);
    const pObj = partOptions.parts.find((p) => p.code === newPart);
    if (pObj && !pObj.sided) {
      setSide("not_applicable");
    } else {
      setSide("");
    }
  }

  function send(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setFormError("");

    if (!confirmIdentity && !evaluateCoverage) {
      setFormError("Select at least one action: Confirm part identity or Evaluate coverage.");
      return;
    }

    const f = new FormData(e.currentTarget);
    const partCode = f.get("part") as string;
    const chosenSide = f.get("side") as string;
    const photoIds = f.getAll("photos");
    const note = String(f.get("note") || "").trim() || null;

    if (!partCode) {
      setFormError("Choose a part.");
      return;
    }
    if (!chosenSide) {
      setFormError("Choose the physical side.");
      return;
    }
    if (photoIds.length === 0) {
      setFormError("Select at least one photograph.");
      return;
    }

    const actions: Record<string, unknown>[] = [];

    if (confirmIdentity) {
      actions.push({
        action_type: "confirm_identity",
        part_code: partCode,
        side: chosenSide,
        photo_ids: photoIds,
        note: note,
      });
    }

    if (evaluateCoverage) {
      if (coverageState === "inadequate" && !insufficientReason) {
        setFormError("Choose a reason when coverage is insufficient.");
        return;
      }
      actions.push({
        action_type: "confirm_coverage",
        part_code: partCode,
        side: chosenSide,
        photo_ids: photoIds,
        covers_enough: coverageState === "adequate",
        reason_code: coverageState === "inadequate" ? insufficientReason : null,
        note: note,
      });
    }

    if (actions.length === 1) {
      void submit(actions[0]);
    } else if (actions.length > 1) {
      void submit(actions);
    }
  }

  return (
    <form onSubmit={send}>
      <fieldset disabled={locked}>
        <legend>Confirm physical identity and coverage</legend>
        <label>
          Part
          <select
            name="part"
            required
            value={part}
            onChange={(e) => handlePartChange(e.target.value)}
          >
            <option value="" disabled>
              Choose a part...
            </option>
            {partOptions.parts.map((p) => (
              <option key={p.code} value={p.code}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Physical side
          <select
            name="side"
            required
            value={side}
            onChange={(e) => setSide(e.target.value)}
          >
            <option value="" disabled>
              Choose the physical side...
            </option>
            {selectedPart
              ? (selectedPart.sided ? ["left", "right"] : ["not_applicable"]).map(
                  (s) => (
                    <option key={s} value={s}>
                      {words(s)}
                    </option>
                  ),
                )
              : null}
          </select>
        </label>
        <p className="muted">
          Left and right are from the vehicle’s perspective. For an unsided part
          such as the windshield, explicitly choose “not applicable”. If you
          cannot identify the part or side, leave it unconfirmed.
        </p>

        {part && side && hasConfirmedCoverage && !isSlotRetracted && (
          <div className="existing-confirmation-notice">
            <div className="existing-confirmation-header">
              <AlertCircle size={16} />
              <span>
                Currently confirmed: <strong>{words(part)} ({words(side)})</strong> &mdash; Coverage: <strong>{words(slotState)}</strong>
              </span>
            </div>
            <p className="muted" style={{ margin: "4px 0 8px 0", fontSize: "0.82rem" }}>
              To revoke this confirmation without violating database immutability, retracting records an audit-compliant superseding event marking coverage as inadequate.
            </p>
            <button
              type="button"
              className="button button-outline button-small button-danger-outline"
              disabled={locked}
              onClick={() => {
                const validPhotos =
                  matchingSlot.covering_photo_ids && matchingSlot.covering_photo_ids.length > 0
                    ? matchingSlot.covering_photo_ids
                    : photos.length > 0
                    ? [photos[0].id]
                    : [];
                if (!validPhotos.length) {
                  setFormError("No photographs available to record retraction.");
                  return;
                }
                void submit({
                  action_type: "confirm_coverage",
                  part_code: part,
                  side: side,
                  photo_ids: validPhotos,
                  covers_enough: false,
                  reason_code: "surveyor_retracted",
                  note: "Confirmation retracted by surveyor",
                });
              }}
            >
              <Undo2 size={13} style={{ marginRight: "4px" }} />
              Retract Confirmation
            </button>
          </div>
        )}

        {part && side && isSlotRetracted && (
          <div className="existing-confirmation-notice notice-retracted">
            <div className="existing-confirmation-header">
              <CheckCircle2 size={16} />
              <span>
                Previous confirmation for <strong>{words(part)} ({words(side)})</strong> was <strong>retracted</strong> (coverage marked inadequate).
              </span>
            </div>
            <p className="muted" style={{ margin: "4px 0 0 0", fontSize: "0.82rem" }}>
              You can submit a new confirmation above at any time to re-establish adequate coverage.
            </p>
          </div>
        )}

        <label>
          Photographs (select one or more)
          <select
            name="photos"
            multiple
            required
            size={Math.min(Math.max(photos.length, 3), 6)}
          >
            {photos.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <p className="muted">
          Hold Command (Mac) or Ctrl (Windows) to select multiple photographs.
        </p>

        <div className="confirmation-card">
          <span className="confirmation-heading">Confirmations to record</span>

          <label className={`confirmation-option ${confirmIdentity ? "is-selected" : ""}`}>
            <input
              type="checkbox"
              checked={confirmIdentity}
              onChange={(e) => setConfirmIdentity(e.target.checked)}
            />
            <div className="confirmation-option-body">
              <span className="confirmation-option-title">Confirm part identity</span>
              <span className="confirmation-option-desc">
                Grounds these photographs to this physical vehicle part
              </span>
            </div>
          </label>

          <label className={`confirmation-option ${evaluateCoverage ? "is-selected" : ""}`}>
            <input
              type="checkbox"
              checked={evaluateCoverage}
              onChange={(e) => setEvaluateCoverage(e.target.checked)}
            />
            <div className="confirmation-option-body">
              <span className="confirmation-option-title">Evaluate photographic coverage</span>
              <span className="confirmation-option-desc">
                Certifies whether views are sufficient to judge damage
              </span>
            </div>
          </label>

          {evaluateCoverage && (
            <div className="coverage-subpanel">
              <span className="coverage-subpanel-title">Coverage Sufficiency</span>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                <label className={`coverage-radio-option ${coverageState === "adequate" ? "is-selected" : ""}`}>
                  <input
                    type="radio"
                    name="coverage_state"
                    value="adequate"
                    checked={coverageState === "adequate"}
                    onChange={() => setCoverageState("adequate")}
                  />
                  <div className="confirmation-option-body">
                    <div className="coverage-radio-header">
                      <span className="confirmation-option-title">Adequate</span>
                      <span className="coverage-badge-recommended">Recommended</span>
                    </div>
                    <span className="confirmation-option-desc">
                      These views show enough of this part to assess damage
                    </span>
                  </div>
                </label>

                <label className={`coverage-radio-option ${coverageState === "inadequate" ? "is-selected" : ""}`}>
                  <input
                    type="radio"
                    name="coverage_state"
                    value="inadequate"
                    checked={coverageState === "inadequate"}
                    onChange={() => setCoverageState("inadequate")}
                  />
                  <div className="confirmation-option-body">
                    <span className="confirmation-option-title">Inadequate</span>
                    <span className="confirmation-option-desc">
                      Coverage is insufficient or obscured
                    </span>
                  </div>
                </label>
              </div>

              {coverageState === "inadequate" && (
                <div className="coverage-reason-box">
                  <label>
                    Why is coverage insufficient?
                    <select
                      name="reason"
                      required
                      value={insufficientReason}
                      onChange={(e) => setInsufficientReason(e.target.value)}
                    >
                      <option value="" disabled>
                        Choose a reason...
                      </option>
                      <option value="surveyor_retracted">
                        Confirmation retracted by surveyor
                      </option>
                      <option value="partially_visible">
                        The part is only partially visible
                      </option>
                      <option value="part_obscured">The part is obscured</option>
                      <option value="image_unclear">
                        The photographs are too unclear
                      </option>
                      <option value="part_not_visible">
                        The part is not visible in these photographs
                      </option>
                      <option value="other">
                        Other visibility limitation
                      </option>
                    </select>
                  </label>
                </div>
              )}
            </div>
          )}
        </div>

        {formError && (
          <div className="field-error-banner" role="alert">
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            <span>{formError}</span>
          </div>
        )}

        <label>
          Explanation (optional)
          <textarea
            name="note"
            maxLength={2000}
            placeholder="Write your explanation in ordinary sentences."
          />
        </label>
        <button
          className="button button-dark button-small"
          style={{ alignSelf: "flex-start", marginTop: "4px" }}
        >
          {confirmIdentity && evaluateCoverage
            ? "Save identity & coverage"
            : confirmIdentity
            ? "Save identity"
            : "Save coverage"}
        </button>
      </fieldset>
    </form>
  );
}

export function IdentityControls({
  photoIds,
  files = [],
  uploadedPhotos = false,
  submit,
  locked,
  existingCoverage = [],
}: {
  photoIds: string[];
  files?: { file_id: string; original_name: string }[];
  uploadedPhotos?: boolean;
  submit: SubmitAction;
  locked: boolean;
  existingCoverage?: CoverageSlotSummary[];
}) {
  const [showInfo, setShowInfo] = useState(false);
  const names = new Map(files.map((f) => [f.file_id, f.original_name]));
  const photos = photoIds.map((id) => ({
    id,
    label: uploadedPhotos && names.has(id) ? `${names.get(id)} (${id})` : id,
  }));
  return (
    <details className="review-controls">
      <summary className="manual-grounding-summary">
        <span className="manual-grounding-summary-inner">
          <span>Manual Part Grounding &bull; For parts not auto-detected by AI</span>
          <span
            className="info-popover-wrapper"
            onMouseEnter={() => setShowInfo(true)}
            onMouseLeave={() => setShowInfo(false)}
          >
            <span
              className="info-popover-trigger"
              tabIndex={0}
              role="button"
              aria-label="Information about manual part grounding"
              title="Click or hover to view instructions"
              onClick={(e) => {
                e.stopPropagation();
                e.preventDefault();
                setShowInfo((prev) => !prev);
              }}
            >
              <Info size={14} />
            </span>
            {showInfo && (
              <span
                className="info-popover-card"
                onClick={(e) => {
                  e.stopPropagation();
                  e.preventDefault();
                }}
              >
                <span className="info-popover-header">
                  <Info size={14} color="#0284c7" />
                  <strong>How Manual Grounding Works</strong>
                </span>
                <span className="info-popover-body">
                  <span>
                    {uploadedPhotos
                      ? "Select the uploaded photographs that show the same physical part."
                      : "Fixture photo IDs refer to demonstration evidence. These confirmations do not establish anything about uploaded photographs."}
                  </span>
                  <span>
                    <strong>Identity</strong> confirms which physical part the photographs show.
                  </span>
                  <span>
                    <strong>Coverage</strong> confirms whether enough of it is visible to assess damage.
                  </span>
                  <span className="info-popover-foot">
                    Combining them records both human confirmations in a single action.
                  </span>
                </span>
              </span>
            )}
          </span>
        </span>
      </summary>
      <div className="manual-grounding-banner">
        <strong>Tip for surveyors:</strong> For vehicle parts already detected by the AI models, use the <em>Confirm Identity &amp; Coverage</em> button directly on each card in the <strong>Damage Summary</strong> above. Use this form only to manually ground vehicle parts that were completely missed by automated detection.
      </div>
      <UnifiedIdentityCoverageForm
        photos={photos}
        submit={submit}
        locked={locked}
        existingCoverage={existingCoverage}
      />
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
