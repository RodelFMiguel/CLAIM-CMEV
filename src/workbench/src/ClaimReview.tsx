import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  Link,
  useLocation,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  ArrowLeft,
  Camera,
  CheckCircle2,
  FileText,
  Info,
  Loader2,
  Printer,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { api, ApiError, money, type Claim } from "./api";
import { localValue, storeLocal, type PendingAction } from "./pendingActions";
import {
  RowControls,
  CompletenessControl,
  IdentityControls,
  Additions,
  type ReviewOverlay,
  type ReviewRow,
} from "./ReviewControls";
import { ErrorBanner, Loading, Status } from "./App";

interface Evidence {
  file_id: string;
  original_name: string;
  media_type: string;
  role: string;
  url: string;
}
interface LineItem extends ReviewRow {
  cost_range: { lower: string; upper: string; support: number | null } | null;
  entry_id: string;
  description: string;
  part_code: string;
  side: string;
  operation: string;
  printed_amount: string | null;
  effective_amount: string | null;
  mark_state: string;
  photo_check: string;
  cost_check: string;
  overall_result: string;
  reason: string;
  evidence_ids: string[];
}
interface Mark {
  mark_id: string;
  entry_id: string | null;
  page_id: string;
  candidate_entry_ids: string[];
  type: string;
  state: string;
  reason: string;
}
interface Action {
  action_type?: string;
  new_values?: Record<string, unknown>;
  reason_code?: string;
  action_id: string;
  type: string;
  text?: string;
  actor: string;
  created_at: string;
  decision?: string;
  amount?: string;
  mark_id?: string;
}
interface Review {
  review_revision: number;
  assessment_revision: number;
  finalized: boolean;
  finalized_at?: string;
  actions: Action[];
}
interface Assessment {
  review_overlay: ReviewOverlay;
  review_photo_ids: string[];
  possible_additions: {
    candidate_id: string;
    part_code: string | null;
    side: string;
    status: string;
    reason: { message: string };
  }[];
  missing_repairs_check: { result: string; reasons: { message: string }[] };
  assessment_revision: number;
  input_revision: number;
  state: string;
  line_items: LineItem[];
  marks: Mark[];
  files: Evidence[];
  damage_summary: {
    part_code: string;
    side: string;
    coverage: string;
    reason: string;
    photo_count: number;
  }[];
  versions: Record<string, string>;
  declaration_completeness: string;
  fixture_notice: string;
  finalize_preconditions: {
    can_finalize: boolean;
    checks: { code: string; passed: boolean; message: string }[];
  };
}
interface Processing {
  state: string;
  jobs: { job_key: string; state: string; stage?: string; error?: string }[];
}
interface Snapshot {
  report?: {
    accepted_additions: {
      action_id: string;
      part_code: string;
      side: string;
      operation: string;
      quantity: string;
      amount: string | null;
      amount_absent_reason: string | null;
      currency: string;
    }[];
    line_items: {
      entry_id: string;
      dismissal: { reason_code: string; note: string | null } | null;
      cost_check: {
        result: string;
        lower_amount: string | null;
        upper_amount: string | null;
        independent_base_case_count: number | null;
      } | null;
    }[];
    mark_decisions: {
      mark_id: string;
      mark_type: string;
      state: string;
      origin: string;
      confirmed_amount: string | null;
      decided_by: string | null;
    }[];
    open_additions: {
      candidate_id: string;
      part_code: string;
      side: string;
      reason: { text: string };
    }[];
    withheld_additions: {
      candidate_id: string;
      part_code: string;
      side: string;
      reason: { text: string };
    }[];
  };
  claim: Claim;
  assessment: Assessment;
  review: Review;
  disclaimer: string;
}
const resultLabel = (item: LineItem) =>
  item.row_state === "excluded"
    ? "Excluded by surveyor"
    : ({
        ok: "No discrepancy found",
        unsupported: "No supporting damage detected in adequate views",
        cost_outlier: "Cost outside reference range",
        insufficient_evidence: "More information needed",
      }[item.overall_result] ?? "Not evaluated");
const actionText = (a: Action) =>
  a.type === "note"
    ? a.text
    : [
        a.action_type?.replaceAll("_", " ") ?? a.type,
        a.reason_code?.replaceAll("_", " "),
        a.new_values
          ? Object.entries(a.new_values)
              .filter(([key]) =>
                [
                  "state",
                  "amount",
                  "part_code",
                  "side",
                  "operation",
                  "quantity",
                  "printed_line_amount",
                ].includes(key),
              )
              .map(
                ([key, value]) =>
                  key.replaceAll("_", " ") +
                  ": " +
                  String(value ?? "not supplied").replaceAll("_", " "),
              )
              .join(", ")
          : "",
        a.text,
      ]
        .filter(Boolean)
        .join(" - ");
const label = (value: string) =>
  value.replaceAll("_", " ").replaceAll("-", " ");

export default function ClaimReview() {
  const { id } = useParams();
  const location = useLocation();
  return location.pathname.endsWith("/print") ? (
    <PrintReport id={id!} />
  ) : (
    <ReviewOverview key={id} id={id!} />
  );
}

function ReviewOverview({ id }: { id: string }) {
  const [claim, setClaim] = useState<Claim | null>(null);
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [processing, setProcessing] = useState<Processing | null>(null);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [selectedRow, setSelectedRow] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [actor, setActor] = useState("");
  const [localReady, setLocalReady] = useState(false);
  const queueKey = "pending:" + actor + ":" + id;
  useEffect(() => {
    let active = true;
    api<{ id: string }>("/auth/me")
      .then(async (user) => {
        const [saved, draft] = await Promise.all([
          localValue<PendingAction>("pending:" + user.id + ":" + id),
          localValue<{ note: string; amounts: Record<string, string> }>(
            "draft:" + user.id + ":" + id,
          ),
        ]);
        if (!active) return;
        setActor(user.id);
        setPending(saved ?? null);
        if (draft) {
          setNote(draft.note);
          setAmounts(draft.amounts);
        }
        setLocalReady(true);
      })
      .catch(() =>
        setError(
          "Local review storage is unavailable. Enable browser storage before saving.",
        ),
      );
    return () => {
      active = false;
    };
  }, [id]);
  useEffect(() => {
    if (localReady)
      void storeLocal("draft:" + actor + ":" + id, { note, amounts }).catch(
        () => setError("The draft could not be stored in this browser."),
      );
  }, [actor, id, localReady, note, amounts]);
  const mounted = useRef(true);
  const started = useRef(Date.now());
  const base = `/claims/${id}`;
  const load = useCallback(async () => {
    const current = await api<Claim>(base);
    let nextAssessment: Assessment | null = null;
    let nextReview: Review | null = null;
    let nextProcessing: Processing | null = null;
    if (current.assessment_revision !== null) {
      [nextAssessment, nextReview] = await Promise.all([
        api<Assessment>(`${base}/assessments/${current.assessment_revision}`),
        api<Review>(
          `${base}/assessments/${current.assessment_revision}/review`,
        ),
      ]);
    } else if (current.status !== "awaiting_upload") {
      nextProcessing = await api<Processing>(`${base}/processing`);
    }
    if (!mounted.current) return;
    setClaim(current);
    setAssessment(nextAssessment);
    setReview(nextReview);
    setProcessing(nextProcessing);
  }, [base]);
  useEffect(() => {
    mounted.current = true;
    load().catch((e) => setError(e.message));
    return () => {
      mounted.current = false;
    };
  }, [load]);
  useEffect(() => {
    if (claim?.status !== "processing" || error) return;
    const timer = window.setTimeout(() => {
      if (Date.now() - started.current > 15 * 60 * 1000) {
        setError(
          "Automatic refresh paused. Refresh the claim to check processing again.",
        );
      } else {
        load().catch((e) => setError(`Connection interrupted. ${e.message}`));
      }
    }, 3000);
    return () => clearTimeout(timer);
  }, [claim, error, load]);

  async function reload() {
    setError("");
    setConflict(false);
    started.current = Date.now();
    try {
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Refresh failed.");
    }
  }
  async function sendPending(action: PendingAction) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await storeLocal(queueKey, action); // durable before transmission
      setPending(action);
      await api(action.path, {
        method: "POST",
        body: JSON.stringify(action.payload),
        headers: { "Idempotency-Key": action.key },
        signal: AbortSignal.timeout(30000),
      });
      await storeLocal(queueKey);
      setPending(null);
      setConflict(false);
      setNotice(action.success);
      if (
        action.path.endsWith("/notes") &&
        (action.payload as { text?: string }).text === note.trim()
      )
        setNote("");
      await load();
      return true;
    } catch (e) {
      setConflict(e instanceof ApiError && e.status === 409);
      if (e instanceof ApiError && [400, 404, 422].includes(e.status)) {
        await storeLocal(queueKey);
        setPending(null);
      }
      setError(
        e instanceof Error
          ? e.message
          : "The action is saved locally. Retry with the same values.",
      );
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function mutate(path: string, payload: unknown, success: string) {
    if (!localReady) return false;
    if (pending) {
      setError("Resolve the saved request before submitting another action.");
      return false;
    }
    return sendPending({
      key: crypto.randomUUID(),
      path,
      payload,
      actor,
      claimId: id,
      success,
    });
  }
  async function reviewAction(values: Record<string, unknown>) {
    if (!assessment || !review || !claim) return false;
    return mutate(
      base +
        "/assessments/" +
        assessment.assessment_revision +
        "/review-actions",
      {
        ...values,
        expected_input_revision: claim.input_revision,
        expected_review_revision: review.review_revision,
      },
      "Review action saved.",
    );
  }
  async function markDecision(mark: Mark, decision: "confirm" | "reject") {
    if (!claim || !review || !assessment) return;
    await mutate(
      `${base}/assessments/${assessment.assessment_revision}/marks/${mark.mark_id}/decision`,
      {
        expected_input_revision: claim.input_revision,
        expected_review_revision: review.review_revision,
        decision,
        amount:
          decision === "confirm" && mark.type === "price_change"
            ? amounts[mark.mark_id]
            : null,
      },
      "Decision saved. A new assessment is being prepared.",
    );
  }
  async function saveNote(event: FormEvent) {
    event.preventDefault();
    if (!assessment || !review || !note.trim()) return;
    if (
      await mutate(
        `${base}/assessments/${assessment.assessment_revision}/notes`,
        { expected_review_revision: review.review_revision, text: note.trim() },
        "Review note saved.",
      )
    )
      setNote("");
  }
  if (!claim)
    return error ? (
      <>
        <ErrorBanner message={error} />
        <button className="button button-dark" onClick={reload}>
          Try again
        </button>
      </>
    ) : (
      <Loading />
    );
  const files = assessment?.files ?? [];
  const file = files.find((f) => f.file_id === selectedFile) ?? files[0];
  const row = assessment?.line_items.find((r) => r.entry_id === selectedRow);
  const frozen = review?.finalized ?? false;
  const locked = busy || conflict || frozen || !!pending || !localReady;
  const blockers =
    assessment?.finalize_preconditions.checks.filter((c) => !c.passed) ?? [];
  return (
    <>
      <Link className="back-link" to="/claims">
        <ArrowLeft size={16} /> Claim queue
      </Link>
      <div className="page-top review-top">
        <div>
          <div className="eyebrow">CLAIM REVIEW</div>
          <h1>
            {claim.reference}
            <span className="title-dot">.</span>
          </h1>
          <p>
            {claim.vehicle.make} {claim.vehicle.model} ({claim.vehicle.year})
            &middot; {claim.vehicle.plate} &middot; {claim.workshop}
          </p>
        </div>
        <div className="review-header-actions">
          <Status value={claim.status} />
          <button
            className="button button-outline button-small"
            onClick={reload}
            disabled={busy}
          >
            <RefreshCw size={15} /> Refresh
          </button>
        </div>
      </div>
      <div className="revision-strip">
        <span>
          Input <b>{claim.input_revision}</b>
        </span>
        <span>
          Assessment <b>{claim.assessment_revision ?? "Pending"}</b>
        </span>
        <span>
          Review <b>{review?.review_revision ?? claim.review_revision}</b>
        </span>
        <span>
          Currency <b>{claim.currency}</b>
        </span>
      </div>
      <div className="fixture-banner">
        <Info size={18} />
        <p>
          <strong>Demonstration results.</strong>{" "}
          {assessment?.fixture_notice ??
            "Processing uses illustrative fixtures. Uploaded evidence is preserved but is not analysed by trained models."}
        </p>
      </div>
      {error && <ErrorBanner message={error} />}
      {pending && (
        <section className="panel" aria-label="Saved pending request">
          <h2>Request awaiting acknowledgement</h2>
          <p>
            The original values and request key are saved in this browser. Retry
            checks the same request, including if it already committed.
          </p>
          <details>
            <summary>Saved request values</summary>
            <pre>{JSON.stringify(pending.payload, null, 2)}</pre>
          </details>
          <button
            className="button button-dark button-small"
            disabled={busy}
            onClick={() => sendPending(pending)}
          >
            Retry saved request
          </button>
          <button
            className="button button-outline button-small"
            disabled={busy}
            onClick={async () => {
              await storeLocal(queueKey);
              setPending(null);
              await reload();
            }}
          >
            Discard local request and refresh
          </button>
          <small>Discarding does not undo a server action.</small>
        </section>
      )}
      {conflict && (
        <div className="conflict-actions">
          <p>
            Your unsaved entries are preserved. Reload the current revision
            before saving again.
          </p>
          <button className="button button-outline" onClick={reload}>
            Reload current revision
          </button>
        </div>
      )}
      {notice && (
        <div className="success-notice" role="status">
          <CheckCircle2 size={17} />
          {notice}
        </div>
      )}
      {!assessment ? (
        <section className="panel processing-panel">
          <span className="section-icon">
            {claim.status === "processing" ? (
              <Loader2 className="spin" size={24} />
            ) : (
              <Camera size={24} />
            )}
          </span>
          <h2>
            {claim.status === "awaiting_upload"
              ? "Evidence has not been uploaded"
              : claim.status === "failed" || claim.status === "incomplete"
                ? "Processing needs attention"
                : "Preparing your review"}
          </h2>
          <p>
            {claim.status === "awaiting_upload"
              ? "This sample claim has no original files. Start a new claim to try the upload workflow."
              : "The worker prepares a versioned fixture assessment. Results will appear here when processing completes."}
          </p>
          {processing?.jobs.map((job) => (
            <div className="job-state" key={job.job_key}>
              <span>{label(job.stage ?? "Evidence processing")}</span>
              <Status value={job.state} />
              {job.error && <p>{job.error}</p>}
            </div>
          ))}
          {claim.status === "awaiting_upload" && (
            <Link className="button button-dark" to="/claims/new">
              New claim
            </Link>
          )}
          <p className="muted">
            {claim.status === "processing"
              ? "Checking for updates every 3 seconds. An unavailable worker remains pending."
              : "No processing result is available."}
          </p>
        </section>
      ) : (
        <>
          <div className="review-layout">
            <div className="review-main">
              <section className="panel">
                <div className="panel-heading">
                  <span className="section-icon">
                    <Camera size={20} />
                  </span>
                  <div>
                    <h2>Damage summary</h2>
                    <p>Part identity and coverage stay explicit.</p>
                  </div>
                </div>
                {assessment.damage_summary.length ? (
                  assessment.damage_summary.map((part, i) => (
                    <div className="damage-item" key={i}>
                      <div>
                        <strong>{label(part.part_code)}</strong>
                        <span className="muted">Side: {part.side}</span>
                      </div>
                      <span className="result-info">
                        Coverage: {label(part.coverage)}
                      </span>
                      <p>{part.reason}</p>
                      <small>
                        Coverage: {part.coverage} &middot; {part.photo_count}{" "}
                        illustrative photographs
                      </small>
                    </div>
                  ))
                ) : (
                  <p className="muted">
                    No photographic assessment is available. Missing photographs
                    do not establish an unsupported repair.
                  </p>
                )}
                <IdentityControls
                  photoIds={assessment.review_photo_ids}
                  submit={reviewAction}
                  locked={locked}
                />
              </section>
              <section className="panel line-items-panel">
                <div className="panel-heading">
                  <span className="section-icon">
                    <FileText size={20} />
                  </span>
                  <div>
                    <h2>Estimate line items</h2>
                    <p>
                      Declaration completeness:{" "}
                      {assessment.declaration_completeness}. Amounts in{" "}
                      {claim.currency}.
                    </p>
                  </div>
                </div>
                <CompletenessControl submit={reviewAction} locked={locked} />
                {assessment.marks
                  .filter((m) => !m.entry_id && m.state !== "rejected")
                  .map((mark) => (
                    <form
                      key={mark.mark_id}
                      className="mark-form"
                      onSubmit={(e) => {
                        e.preventDefault();
                        const f = new FormData(e.currentTarget);
                        void reviewAction({
                          action_type: "correct_mark_link",
                          mark_id: mark.mark_id,
                          target_entry_id: f.get("entry"),
                        });
                      }}
                    >
                      <p>
                        Unlinked {label(mark.type)}: {mark.reason}
                      </p>
                      <label>
                        Assign mark to row
                        <select
                          name="entry"
                          defaultValue=""
                          required
                          disabled={locked}
                        >
                          <option value="" disabled>
                            Choose a row
                          </option>
                          {assessment.line_items
                            .filter((i) => i.page_id === mark.page_id)
                            .map((i) => (
                              <option key={i.entry_id} value={i.entry_id}>
                                {i.description}
                                {mark.candidate_entry_ids.includes(i.entry_id)
                                  ? " (candidate)"
                                  : ""}
                              </option>
                            ))}
                        </select>
                      </label>
                      <button
                        className="button button-outline button-small"
                        disabled={locked}
                      >
                        Save row link
                      </button>
                      {mark.state === "pending" && (
                        <button
                          type="button"
                          disabled={locked}
                          className="button button-outline button-small"
                          onClick={() =>
                            reviewAction({
                              action_type: "reject_mark",
                              mark_id: mark.mark_id,
                            })
                          }
                        >
                          Reject unlinked mark
                        </button>
                      )}
                    </form>
                  ))}
                <details className="review-controls">
                  <summary>Review all mark links and confirmed amounts</summary>
                  {assessment.marks
                    .filter((m) => m.entry_id && m.state !== "rejected")
                    .map((mark) => (
                      <div key={mark.mark_id}>
                        <p>
                          {label(mark.type)}: {mark.state}, row{" "}
                          {
                            assessment.line_items.find(
                              (i) => i.entry_id === mark.entry_id,
                            )?.description
                          }
                        </p>
                        <form
                          onSubmit={(e) => {
                            e.preventDefault();
                            const f = new FormData(e.currentTarget);
                            void reviewAction({
                              action_type: "correct_mark_link",
                              mark_id: mark.mark_id,
                              target_entry_id: f.get("entry"),
                            });
                          }}
                        >
                          <fieldset disabled={locked}>
                            <label>
                              Correct row link
                              <select
                                name="entry"
                                defaultValue={mark.entry_id ?? ""}
                              >
                                {assessment.line_items
                                  .filter((i) => i.page_id === mark.page_id)
                                  .map((i) => (
                                    <option key={i.entry_id} value={i.entry_id}>
                                      {i.description}
                                    </option>
                                  ))}
                              </select>
                            </label>
                            <button className="button button-outline button-small">
                              Correct mark link
                            </button>
                          </fieldset>
                        </form>
                        {mark.state === "confirmed" &&
                          mark.type === "price_change" && (
                            <form
                              onSubmit={(e) => {
                                e.preventDefault();
                                const f = new FormData(e.currentTarget);
                                void reviewAction({
                                  action_type: "enter_amount",
                                  mark_id: mark.mark_id,
                                  amount: f.get("amount"),
                                });
                              }}
                            >
                              <fieldset disabled={locked}>
                                <label>
                                  Change confirmed amount
                                  <input
                                    name="amount"
                                    type="number"
                                    min="0"
                                    max="999999.99"
                                    step=".01"
                                    required
                                  />
                                </label>
                                <button className="button button-outline button-small">
                                  Save revised amount
                                </button>
                              </fieldset>
                            </form>
                          )}
                      </div>
                    ))}
                </details>
                {assessment.line_items.length ? (
                  assessment.line_items.map((item) => (
                    <article
                      className={`line-item ${selectedRow === item.entry_id ? "selected" : ""}`}
                      key={item.entry_id}
                    >
                      <div className="line-item-title">
                        <div>
                          <h3>
                            {item.row_state === "excluded" ? (
                              <s>{item.description}</s>
                            ) : (
                              item.description
                            )}
                          </h3>
                          <p>
                            {label(item.part_code)} &middot; Side: {item.side}{" "}
                            &middot; {item.operation}
                          </p>
                        </div>
                        <button
                          className="button button-outline button-small"
                          onClick={() => {
                            setSelectedRow(item.entry_id);
                            setSelectedFile(item.evidence_ids[0] ?? null);
                            document.getElementById("evidence-panel")?.focus();
                          }}
                        >
                          View evidence
                        </button>
                      </div>
                      <div className="amount-grid">
                        <div>
                          <span>Printed amount (current extraction)</span>
                          <strong>
                            {money(item.printed_amount, claim.currency)}
                          </strong>
                        </div>
                        <div>
                          <span>Effective surveyor amount</span>
                          <strong>
                            {item.effective_amount === null
                              ? "Withheld"
                              : money(item.effective_amount, claim.currency)}
                          </strong>
                        </div>
                        <div>
                          <span>Pen mark</span>
                          <Status value={item.mark_state} />
                        </div>
                      </div>
                      <div className="line-checks">
                        <span>Photo: {label(item.photo_check)}</span>
                        <span>Cost: {label(item.cost_check)}</span>
                        <span>Single-part basis</span>
                      </div>
                      <div className="result-message">
                        <Info size={16} />
                        <div>
                          <strong>{resultLabel(item)}</strong>
                          <p>{item.reason}</p>
                        </div>
                      </div>
                      {assessment.marks
                        .filter(
                          (m) =>
                            m.entry_id === item.entry_id &&
                            m.state === "pending",
                        )
                        .map((mark) => (
                          <form
                            className="mark-form"
                            key={mark.mark_id}
                            onSubmit={(event) => {
                              event.preventDefault();
                              void markDecision(mark, "confirm");
                            }}
                          >
                            <p>{mark.reason}</p>
                            {mark.type === "price_change" && (
                              <>
                                <label htmlFor={mark.mark_id}>
                                  Revised amount ({claim.currency})
                                </label>
                                <input
                                  id={mark.mark_id}
                                  className="amount-input"
                                  type="number"
                                  min="0"
                                  max="999999.99"
                                  step="0.01"
                                  required
                                  placeholder="Enter the surveyor amount"
                                  value={amounts[mark.mark_id] ?? ""}
                                  onChange={(e) =>
                                    setAmounts({
                                      ...amounts,
                                      [mark.mark_id]: e.target.value,
                                    })
                                  }
                                  disabled={locked}
                                />
                              </>
                            )}
                            <div className="mark-buttons">
                              <button
                                className="button button-dark button-small"
                                disabled={locked}
                              >
                                {mark.type === "exclusion"
                                  ? "Confirm exclusion"
                                  : "Confirm price change"}
                              </button>
                              <button
                                className="button button-outline button-small"
                                type="button"
                                disabled={locked}
                                onClick={() => markDecision(mark, "reject")}
                              >
                                Reject mark
                              </button>
                            </div>
                            <small>
                              Either decision creates a new input revision and
                              reassessment. Rejecting keeps the original printed
                              amount.
                            </small>
                          </form>
                        ))}
                      <RowControls
                        row={item}
                        submit={reviewAction}
                        locked={locked}
                        dismissed={
                          assessment.review_overlay.dismissals[item.finding_id]
                        }
                      />
                    </article>
                  ))
                ) : (
                  <p className="muted">
                    No estimate pages were supplied. There are no extracted rows
                    to review.
                  </p>
                )}
              </section>
              <section className="panel">
                <div className="panel-heading">
                  <span className="section-icon">
                    <ShieldCheck size={20} />
                  </span>
                  <div>
                    <h2>Possible additions</h2>
                    <p>
                      Only supported, unmatched damage can suggest an addition.
                    </p>
                  </div>
                </div>
                <p>
                  Missing-repairs check:{" "}
                  {label(assessment.missing_repairs_check.result)}.{" "}
                  {assessment.missing_repairs_check.reasons
                    .map((r) => r.message)
                    .join(" ")}
                </p>
                <Additions
                  candidates={assessment.possible_additions}
                  overlay={assessment.review_overlay}
                  submit={reviewAction}
                  locked={locked}
                />
              </section>
              <section className="panel">
                <div className="panel-heading">
                  <span className="section-icon">
                    <FileText size={20} />
                  </span>
                  <div>
                    <h2>Review notes</h2>
                    <p>Saved with the actor, time and review revision.</p>
                  </div>
                </div>
                {review?.actions.length ? (
                  <ol className="review-history">
                    {review.actions.map((action) => (
                      <li key={action.action_id}>
                        <strong>{action.actor}</strong>
                        <time>
                          {new Date(action.created_at).toLocaleString()}
                        </time>
                        <p>{actionText(action)}</p>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="muted">No review actions recorded yet.</p>
                )}
                {!frozen && (
                  <form className="note-form" onSubmit={saveNote}>
                    <label htmlFor="review-note">Add note</label>
                    <textarea
                      id="review-note"
                      className="note-area"
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      maxLength={2000}
                      rows={3}
                      required
                      disabled={locked}
                      placeholder="Record your review context..."
                    />
                    <button
                      className="button button-outline button-small"
                      disabled={locked || !note.trim()}
                    >
                      Save note
                    </button>
                  </form>
                )}
              </section>
            </div>
            <aside
              className="evidence-panel panel"
              id="evidence-panel"
              tabIndex={-1}
            >
              <div className="panel-heading">
                <span className="section-icon">
                  <FileText size={20} />
                </span>
                <div>
                  <h2>Evidence</h2>
                  <p>{row ? row.description : "Preserved original files"}</p>
                </div>
              </div>
              {files.length ? (
                <>
                  <label className="evidence-select">
                    Original file
                    <select
                      value={file?.file_id ?? ""}
                      onChange={(e) => setSelectedFile(e.target.value)}
                    >
                      {files.map((f) => (
                        <option value={f.file_id} key={f.file_id}>
                          {f.original_name}
                        </option>
                      ))}
                    </select>
                  </label>
                  {file && (
                    <>
                      <div className="evidence-preview">
                        {file.media_type === "application/pdf" ? (
                          <iframe
                            title={`Original estimate: ${file.original_name}`}
                            src={file.url}
                          />
                        ) : (
                          <img
                            src={file.url}
                            alt={`Original evidence: ${file.original_name}`}
                            onError={(e) => {
                              e.currentTarget.style.display = "none";
                              setError(
                                "The original image could not be loaded. Use the original-file link or refresh.",
                              );
                            }}
                          />
                        )}
                      </div>
                      <a
                        className="button button-outline button-small"
                        href={file.url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Open original file
                      </a>
                      <p className="evidence-caption">{file.original_name}</p>
                    </>
                  )}
                </>
              ) : (
                <div className="evidence-empty">
                  <Camera size={32} />
                  <h3>No original files</h3>
                  <p>
                    This seeded example has illustrative counts only. Upload a
                    new claim to inspect your own evidence here.
                  </p>
                </div>
              )}
              <div className="evidence-cost">
                <h3>Synthetic cost reference</h3>
                <p>
                  Synthetic ranges are engineering references; they do not
                  validate actual repair prices.
                </p>
                <dl>
                  <dt>Table</dt>
                  <dd>{assessment.versions.cost_table}</dd>
                  <dt>Basis</dt>
                  <dd>One part &middot; SGD</dd>
                  <dt>Range / support</dt>
                  <dd>
                    {row?.cost_range
                      ? money(row.cost_range.lower) +
                        " to " +
                        money(row.cost_range.upper) +
                        " / " +
                        (row.cost_range.support ?? "unknown") +
                        " independent cases"
                      : "Select a row with an evaluated cost check"}
                  </dd>
                </dl>
              </div>
              <div className="inline-info">
                <Info size={16} />
                <span>
                  Mock processing does not produce evidence overlays or validate
                  repair prices.
                </span>
              </div>
            </aside>
          </div>
          <section className="panel finalize-panel">
            <div>
              <h2>{frozen ? "Review finalized" : "Ready to finalize?"}</h2>
              <p>
                {frozen
                  ? "This review is frozen. The report uses exactly this assessment and review revision."
                  : "Freeze this completed assessment and review for the printable report."}
              </p>
              {blockers.length > 0 && !frozen && (
                <ul className="blocker-list">
                  {blockers.map((check) => (
                    <li key={check.code}>{check.message}</li>
                  ))}
                </ul>
              )}
              <small>
                More information needed remains visible in the report. Final
                claim approval is not recorded.
              </small>
            </div>
            {frozen ? (
              <Link
                className="button button-dark"
                to={`${base}/print?assessment=${assessment.assessment_revision}&review=${review!.review_revision}`}
              >
                <Printer size={17} /> Print report
              </Link>
            ) : (
              <button
                className="button button-dark"
                disabled={
                  locked || !assessment.finalize_preconditions.can_finalize
                }
                onClick={() =>
                  mutate(
                    `${base}/assessments/${assessment.assessment_revision}/finalize`,
                    { expected_review_revision: review!.review_revision },
                    "Review finalized. Your frozen report is ready.",
                  )
                }
              >
                <CheckCircle2 size={17} /> Finalize review
              </button>
            )}
          </section>
          <div className="version-footer">
            {Object.entries(assessment.versions).map(([key, value]) => (
              <span key={key}>
                {label(key)}: {value}
              </span>
            ))}
          </div>
        </>
      )}
    </>
  );
}

function PrintReport({ id }: { id: string }) {
  const [params] = useSearchParams();
  const assessmentRev = params.get("assessment");
  const reviewRev = params.get("review");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!assessmentRev || !reviewRev) {
      setError(
        "A frozen assessment and review revision are required. Open the report from the finalized review.",
      );
      return;
    }
    let active = true;
    api<Snapshot>(
      `/claims/${id}/assessments/${assessmentRev}/print-view?review_revision=${encodeURIComponent(reviewRev)}`,
    )
      .then((result) => {
        if (active) setSnapshot(result);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [id, assessmentRev, reviewRev]);
  if (error)
    return (
      <>
        <ErrorBanner message={error} />
        <Link className="back-link" to={`/claims/${id}/review`}>
          Return to review
        </Link>
      </>
    );
  if (!snapshot) return <Loading />;
  const { claim, assessment, review } = snapshot;
  const footerText = [
    claim.reference, "Input " + assessment.input_revision, "Assessment " + assessment.assessment_revision,
    "Review " + review.review_revision, "Demonstration fixtures. Reference costs are synthetic.",
    ...Object.entries(assessment.versions).map(([key, value]) => key + ": " + value),
  ].join(" / ");
  const footerCssString = JSON.stringify(footerText);
  return (
    <div className="print-report">
      <style>{'@media print { @page { @bottom-center { content: ' + footerCssString +
        '; font-family: Arial, sans-serif; font-size: 6.5pt; line-height: 1.25; text-align: left; vertical-align: top; padding-top: 3mm; white-space: normal; overflow-wrap: anywhere; } ' +
        '@top-right { content: "Page " counter(page) " of " counter(pages); font-family: Arial, sans-serif; font-size: 8pt; color: #555; } } }'}</style>
      <div className="print-toolbar no-print">
        <Link className="back-link" to={`/claims/${id}/review`}>
          <ArrowLeft size={16} /> Back to review
        </Link>
        <button className="button button-dark" onClick={() => window.print()}>
          <Printer size={17} /> Print to PDF
        </button>
      </div>
      <article className="report-paper">
        <div className="eyebrow">CLAIM-CMEV / FROZEN REVIEW REPORT</div>
        <h1>{claim.reference}</h1>
        <p>
          {claim.vehicle.make} {claim.vehicle.model} ({claim.vehicle.year})
          &middot; {claim.vehicle.plate}
        </p>
        <dl className="report-details">
          <dt>Policy</dt>
          <dd>{claim.policy_number || "Not supplied"}</dd>
          <dt>Workshop</dt>
          <dd>{claim.workshop || "Not supplied"}</dd>
          <dt>Surveyor</dt>
          <dd>{claim.surveyor}</dd>
          <dt>Finalized</dt>
          <dd>
            {review.finalized_at
              ? new Date(review.finalized_at).toLocaleString()
              : "Recorded in frozen revision"}
          </dd>
          <dt>Final claim approval</dt>
          <dd>
            <strong>NOT RECORDED</strong>
          </dd>
        </dl>
        <div className="fixture-banner">
          <Info size={18} />
          <p>{snapshot.disclaimer}</p>
        </div>
        <h2>Estimate and review findings</h2>
        <p className="report-basis">
          Currency: {claim.currency}. Synthetic reference:{" "}
          {assessment.versions.cost_table}. Fixed single-part basis. No price
          validation performed.
        </p>
        <table className="report-table">
          <thead>
            <tr>
              <th>Estimate line</th>
              <th>Printed</th>
              <th>Effective</th>
              <th>Review result</th>
            </tr>
          </thead>
          <tbody>
            {assessment.line_items.map((item) => (
              <tr key={item.entry_id}>
                <td>
                  <strong>{item.description}</strong>
                  <small>
                    Side: {item.side} / {item.operation}
                  </small>
                </td>
                <td>{money(item.printed_amount, claim.currency)}</td>
                <td>{money(item.effective_amount, claim.currency)}</td>
                <td>
                  <strong>{resultLabel(item)}</strong>
                  <small>{item.reason}</small>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!assessment.line_items.length && <p>No estimate rows available.</p>}
        <h2>Photographic coverage</h2>
        {assessment.damage_summary.map((part, i) => (
          <p key={i}>
            {label(part.part_code)} / {label(part.side)}: {label(part.coverage)}
            . {part.reason}
          </p>
        ))}
        <h2>Declaration and missing repairs</h2>
        <p>
          Declaration: {assessment.declaration_completeness}. Missing-repairs
          check: {assessment.missing_repairs_check?.result}.{" "}
          {assessment.missing_repairs_check?.reasons
            .map((r) => r.message)
            .join(" ")}
        </p>
        <h2>Accepted additions</h2>
        {snapshot.report?.accepted_additions.length ? (
          snapshot.report.accepted_additions.map((a) => (
            <p key={a.action_id}>
              {label(a.part_code)} / {label(a.side)}: {a.operation}, quantity{" "}
              {a.quantity}. Surveyor amount:{" "}
              {a.amount === null
                ? "Not supplied: " + a.amount_absent_reason
                : money(a.amount, a.currency)}
              .
            </p>
          ))
        ) : (
          <p>No additions accepted.</p>
        )}
        <h2>Open and withheld additions</h2>
        {[
          ...(snapshot.report?.open_additions ?? []),
          ...(snapshot.report?.withheld_additions ?? []),
        ].map((a) => (
          <p key={a.candidate_id}>
            {a.part_code ?? "Unresolved"} / {a.side}: {a.reason.text}
          </p>
        ))}
        <h2>Pen-mark decisions</h2>
        {snapshot.report?.mark_decisions.map((m) => (
          <p key={m.mark_id}>
            {label(m.mark_type)}: {m.state}, {label(m.origin)}.{" "}
            {m.confirmed_amount !== null
              ? money(m.confirmed_amount, claim.currency)
              : ""}{" "}
            {m.decided_by ?? "No decision recorded"}.
          </p>
        ))}
        <h2>Cost checks and dismissals</h2>
        {snapshot.report?.line_items.map((item) => (
          <p key={item.entry_id}>
            {
              assessment.line_items.find((i) => i.entry_id === item.entry_id)
                ?.description
            }
            : {label(item.cost_check?.result ?? "not_evaluated")}.{" "}
            {item.cost_check?.lower_amount != null && (
              <>
                Synthetic range {money(item.cost_check.lower_amount)} to{" "}
                {money(item.cost_check.upper_amount)};{" "}
                {item.cost_check.independent_base_case_count} independent base
                cases.
              </>
            )}{" "}
            {item.dismissal && (
              <>
                Dismissed: {label(item.dismissal.reason_code)}.{" "}
                {item.dismissal.note}
              </>
            )}
          </p>
        ))}
        <h2>Review history</h2>
        {review.actions.length ? (
          <ol className="review-history">
            {review.actions.map((action) => (
              <li key={action.action_id}>
                <strong>{action.actor}</strong>
                <time>{new Date(action.created_at).toLocaleString()}</time>
                <p>{actionText(action)}</p>
              </li>
            ))}
          </ol>
        ) : (
          <p>No additional review actions recorded.</p>
        )}
        <div className="report-versions">
          <p>
            {claim.reference} &middot; Input {assessment.input_revision}{" "}
            &middot; Assessment {assessment.assessment_revision} &middot; Review{" "}
            {review.review_revision}
          </p>
          <p>
            {Object.entries(assessment.versions)
              .map(([key, value]) => `${key}: ${value}`)
              .join(" / ")}
          </p>
        </div>
      </article>
    </div>
  );
}
