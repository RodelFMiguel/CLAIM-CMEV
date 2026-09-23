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
import { ErrorBanner, Loading, Status } from "./App";

interface Evidence {
  file_id: string;
  original_name: string;
  media_type: string;
  role: string;
  url: string;
}
interface LineItem {
  entry_id: string;
  description: string;
  part_code: string;
  side: string;
  operation: string;
  printed_amount: string;
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
  entry_id: string;
  type: string;
  state: string;
  reason: string;
}
interface Action {
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
  claim: Claim;
  assessment: Assessment;
  review: Review;
  disclaimer: string;
}
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
  const pending = useRef<{ signature: string; key: string } | null>(null);
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
  async function mutate(path: string, payload: unknown, success: string) {
    const signature = JSON.stringify([path, payload]);
    if (pending.current?.signature !== signature)
      pending.current = { signature, key: crypto.randomUUID() };
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(path, {
        method: "POST",
        body: JSON.stringify(payload),
        headers: { "Idempotency-Key": pending.current.key },
      });
      pending.current = null;
      setNotice(success);
      await load();
      return true;
    } catch (e) {
      setConflict(e instanceof ApiError && e.status === 409);
      setError(
        e instanceof Error
          ? e.message
          : "The action could not be saved. Your entry is still here; retry to save it.",
      );
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function markDecision(mark: Mark, decision: "confirm" | "reject") {
    if (!claim || !review || !assessment) return;
    await mutate(
      `${base}/assessments/${assessment.assessment_revision}/marks/${mark.mark_id}/decision`,
      {
        expected_input_revision: claim.input_revision,
        expected_review_revision: review.review_revision,
        decision,
        amount: decision === "confirm" ? amounts[mark.mark_id] : null,
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
  const locked = busy || conflict || frozen;
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
              : claim.status === "failed"
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
                        More information needed
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
                {assessment.line_items.length ? (
                  assessment.line_items.map((item) => (
                    <article
                      className={`line-item ${selectedRow === item.entry_id ? "selected" : ""}`}
                      key={item.entry_id}
                    >
                      <div className="line-item-title">
                        <div>
                          <h3>{item.description}</h3>
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
                          <span>Original printed amount</span>
                          <strong>{money(item.printed_amount)}</strong>
                        </div>
                        <div>
                          <span>Effective surveyor amount</span>
                          <strong>
                            {item.effective_amount === null
                              ? "Withheld"
                              : money(item.effective_amount)}
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
                          <strong>
                            {item.mark_state === "pending"
                              ? "Awaiting mark confirmation"
                              : "More information needed"}
                          </strong>
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
                            <label htmlFor={mark.mark_id}>
                              Revised amount ({claim.currency})
                            </label>
                            <input
                              id={mark.mark_id}
                              className="amount-input"
                              type="number"
                              min="0"
                              max="999999999"
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
                            <div className="mark-buttons">
                              <button
                                className="button button-dark button-small"
                                disabled={locked}
                              >
                                Confirm price change
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
                <p className="muted">
                  Suggestions are withheld in fixture mode. No uploaded evidence
                  has been analysed.
                </p>
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
                        <p>
                          {action.type === "note"
                            ? action.text
                            : `Mark ${action.decision === "confirm" ? "confirmed" : "rejected"}${action.amount ? `: ${money(action.amount)}` : ""}.`}
                        </p>
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
                      maxLength={4000}
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
                  No cost comparison was performed on the uploaded evidence.
                </p>
                <dl>
                  <dt>Table</dt>
                  <dd>{assessment.versions.cost_table}</dd>
                  <dt>Basis</dt>
                  <dd>One part &middot; SGD</dd>
                  <dt>Range / support</dt>
                  <dd>Not evaluated</dd>
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
  return (
    <div className="print-report">
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
                <td>{money(item.printed_amount)}</td>
                <td>{money(item.effective_amount)}</td>
                <td>
                  <strong>More information needed</strong>
                  <small>{item.reason}</small>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!assessment.line_items.length && <p>No estimate rows available.</p>}
        <h2>Review history</h2>
        {review.actions.length ? (
          <ol className="review-history">
            {review.actions.map((action) => (
              <li key={action.action_id}>
                <strong>{action.actor}</strong>
                <time>{new Date(action.created_at).toLocaleString()}</time>
                <p>
                  {action.type === "note"
                    ? action.text
                    : `Mark ${action.mark_id} ${action.decision}${action.amount ? `, amount ${money(action.amount)}` : ""}.`}
                </p>
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
