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
  AlertTriangle,
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  ArrowUpToLine,
  Calculator,
  Camera,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CircleDashed,
  Download,
  ExternalLink,
  FileText,
  Info,
  Loader2,
  Maximize2,
  MinusCircle,
  OctagonAlert,
  Printer,
  RefreshCw,
  ShieldCheck,
  UploadCloud,
} from "lucide-react";
import { api, ApiError, money, type Claim } from "./api";
import {
  localValue,
  savePending,
  removePending,
  loadPending,
  loadDraft,
  saveDraft,
  pruneDrafts,
  draftKey,
  reviewTabId,
  type PendingAction,
} from "./pendingActions";
import {
  RowControls,
  CompletenessControl,
  IdentityControls,
  Additions,
  type DeclarationData,
  type ReviewOverlay,
  type ReviewRow,
} from "./ReviewControls";
import { ErrorBanner, Loading, Status } from "./App";
import {
  DamageSummary,
  reasonText,
  type DamageRow,
  type DamageGroup,
  type DamageObservation,
} from "./DamageSummary";
import {
  CorrectedPageView,
  EvidenceDialog,
  EvidenceFile,
  EvidenceImage,
} from "./EvidenceDialog";
import { SupplementaryEvidenceModal } from "./SupplementaryEvidenceModal";
import { SupplementaryReopeningModal } from "./SupplementaryReopeningModal";

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
  original_printed_amount?: string | null;
  printed_amount_corrected?: boolean;
  original_amount_text?: string | null;
  effective_amount: string | null;
  effective_price_source?: string | null;
  effective_price_reason?: string | null;
  mark_state: string;
  photo_check: string;
  cost_check: string;
  overall_result: string;
  reason: string;
  evidence_ids: string[];
}
interface Mark {
  box_norm: number[];
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
  report?: {
    header?: {
      reviewed_by?: string;
      finalized_at?: string;
      [key: string]: unknown;
    };
    [key: string]: unknown;
  };
}
interface DocumentPage {
  page_id: string;
  file_id: string;
  page_number: number;
  corrected_url?: string | null;
}
interface Assessment {
  pages?: DocumentPage[];
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
  damage_summary: DamageRow[];
  part_summaries?: DamageGroup[];
  coverage?: { part_code: string; side: string; state: string; reasons?: string[]; covering_photo_ids?: string[] }[];
  observations?: DamageObservation[];
  // "real" when trained models or parsers read the uploaded evidence; an older API omits it.
  evidence_sources?: { image: string; document: string };
  versions: Record<string, string>;
  declaration_completeness: string;
  declaration?: DeclarationData;
  fixture_notice: string | null;
  invalidated_corrections?: unknown[];
  finalize_preconditions: {
    can_finalize: boolean;
    checks: { code: string; passed: boolean; message: string }[];
  };
}
interface Processing {
  state: string;
  jobs: {
    job_key: string;
    state: string;
    stage?: string;
    error?: string;
    retryable?: boolean;
  }[];
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
const RESULTS = ["ok", "unsupported", "cost_outlier", "insufficient_evidence"];
// Each result differs in colour role, border style, icon shape and wording
// (UI specification section 7.3). Excluded and not-evaluated rows are row
// states, not findings: they get a neutral note and no result chip.
function ResultDisplay({ item }: { item: LineItem }) {
  if (item.row_state === "excluded" || !RESULTS.includes(item.overall_result))
    return (
      <div
        className={`row-state-note ${item.row_state === "excluded" ? "row-state-excluded" : "row-state-not-evaluated"}`}
      >
        {item.row_state === "excluded" ? (
          <MinusCircle size={16} aria-hidden="true" />
        ) : (
          <CircleDashed size={16} aria-hidden="true" />
        )}
        <div>
          <strong>{resultLabel(item)}</strong>
          <p>{item.reason}</p>
        </div>
      </div>
    );
  // Arrow against a bar: below the lower bound points down, otherwise up.
  const below =
    item.cost_range != null &&
    item.effective_amount != null &&
    Number(item.effective_amount) < Number(item.cost_range.lower);
  const icon =
    item.overall_result === "ok" ? (
      <CheckCircle2 size={16} aria-hidden="true" />
    ) : item.overall_result === "insufficient_evidence" ? (
      <Info size={16} aria-hidden="true" />
    ) : item.overall_result === "cost_outlier" ? (
      below ? (
        <ArrowDownToLine size={16} aria-hidden="true" />
      ) : (
        <ArrowUpToLine size={16} aria-hidden="true" />
      )
    ) : (
      <OctagonAlert size={16} aria-hidden="true" />
    );
  return (
    <div
      className={`result-message result-${item.overall_result}`}
      data-result={item.overall_result}
    >
      {icon}
      <div>
        <strong>{resultLabel(item)}</strong>
        <p>{item.reason}</p>
      </div>
    </div>
  );
}
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

// Mark states that leave a row's amount undecided (WITHHOLDING_STATES in documents/pen_marks/rows.py).
const UNDECIDED_MARK_STATES = new Set(["pending", "conflicting", "unlinked_candidate"]);

/** The agreed total, or null while any amount is undecided: a withheld amount never counts as
 *  zero and a pending price change never falls back to the printed amount. Confirmed
 *  exclusions count as zero. Mirrors `_compute_effective_total` in api/views.py. */
function agreedTotal(items: LineItem[], marks: Mark[] = []) {
  let total = 0;
  let undecidedRows = 0;
  for (const item of items) {
    if (item.row_state === "excluded") continue;
    if (UNDECIDED_MARK_STATES.has(item.mark_state) || item.effective_amount == null) undecidedRows += 1;
    else total += Number(item.effective_amount);
  }
  const unlinkedMarks = marks.filter((m) => m.entry_id == null && m.state !== "rejected").length;
  return { total: undecidedRows || unlinkedMarks ? null : total, undecidedRows, unlinkedMarks };
}

function undecidedText({ undecidedRows, unlinkedMarks }: ReturnType<typeof agreedTotal>) {
  return [
    undecidedRows && `${undecidedRows} row${undecidedRows === 1 ? "" : "s"} awaiting a decision`,
    unlinkedMarks && `${unlinkedMarks} unlinked mark${unlinkedMarks === 1 ? "" : "s"}`,
  ]
    .filter(Boolean)
    .join(" · ");
}

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
  const [selectedPage, setSelectedPage] = useState<string | null>(null);
  const [selectedRow, setSelectedRow] = useState<string | null>(null);
  const [rowPopup, setRowPopup] = useState(false);
  const [showEvidenceModal, setShowEvidenceModal] = useState(false);
  const [showReopeningModal, setShowReopeningModal] = useState(false);
  const [evidenceTab, setEvidenceTab] = useState<"photos" | "pages">("photos");
  const [popupEvidence, setPopupEvidence] = useState<{
    kind: "file";
    file: Evidence;
  } | {
    kind: "page";
    page: DocumentPage;
    name: string;
  } | null>(null);
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [actor, setActor] = useState("");
  const [localReady, setLocalReady] = useState(false);
  const [tabId, setTabId] = useState("");
  const [expandReviewHistory, setExpandReviewHistory] = useState(false);
  useEffect(() => {
    void reviewTabId()
      .then(setTabId)
      .catch(() =>
        setError(
          "Local review storage is unavailable. Enable browser storage before saving.",
        ),
      );
  }, []);
  const [pollError, setPollError] = useState("");
  const queueKey = "pending:" + actor + ":" + id;
  useEffect(() => {
    if (!tabId) return;
    let active = true;
    api<{ user: { id: string } }>("/auth/me")
      .then(async ({ user }) => {
        const [saved, draft] = await Promise.all([
          loadPending("pending:" + user.id + ":" + id, user.id, id),
          loadDraft(user.id, id, tabId),
        ]);
        // Housekeeping only: a pruning failure must not block the review.
        void pruneDrafts(draftKey(user.id, id, tabId)).catch(() => undefined);
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
  }, [id, tabId]);
  useEffect(() => {
    if (localReady)
      void saveDraft(draftKey(actor, id, tabId), {
        note,
        amounts,
      }).catch(() =>
        setError("The draft could not be stored in this browser."),
      );
  }, [actor, id, tabId, localReady, note, amounts]);
  const mounted = useRef(true);
  const base = `/claims/${id}`;
  // After a failed reassessment the claim has no current assessment. The
  // surveyor may open the latest one read-only; it is never finalizable.
  const [showPrior, setShowPrior] = useState(false);
  const [processingWarning, setProcessingWarning] = useState("");
  const load = useCallback(async () => {
    const current = await api<Claim>(base);
    let nextAssessment: Assessment | null = null;
    let nextReview: Review | null = null;
    const revision =
      current.assessment_revision ??
      (showPrior ? (current.latest_assessment_revision ?? null) : null);
    const wantsProcessing =
      current.status !== "awaiting_upload" && current.status !== "finalized";
    // Processing status is supplementary: its failure must not block a review
    // whose assessment and review loaded. The previous status stays shown.
    let processingFailure = "";
    const processingRequest = wantsProcessing
      ? api<Processing>(`${base}/processing`).catch((e) => {
          processingFailure = e instanceof Error ? e.message : "unavailable";
          return undefined;
        })
      : Promise.resolve(null);
    if (revision !== null) {
      [nextAssessment, nextReview] = await Promise.all([
        api<Assessment>(`${base}/assessments/${revision}`),
        api<Review>(`${base}/assessments/${revision}/review`),
      ]);
    }
    const nextProcessing = await processingRequest;
    if (!mounted.current) return;
    setPollError("");
    setClaim(current);
    setAssessment(nextAssessment);
    setReview(nextReview);
    if (nextProcessing !== undefined) setProcessing(nextProcessing);
    setProcessingWarning(
      processingFailure
        ? "Processing status could not be loaded. The review below is still available; stage retry controls may be out of date. " +
            processingFailure
        : "",
    );
  }, [base, showPrior]);
  useEffect(() => {
    mounted.current = true;
    load().catch((e) => setError(e.message));
    return () => {
      mounted.current = false;
    };
  }, [load]);
  useEffect(() => {
    if (claim?.status !== "processing") return;
    let loading = false;
    const timer = window.setInterval(async () => {
      if (loading) return;
      loading = true;
      try {
        await load();
      } catch (e) {
        if (mounted.current)
          setPollError(
            `Connection interrupted. Retrying automatically. ${e instanceof Error ? e.message : ""}`,
          );
      } finally {
        loading = false;
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [claim?.status, load]);

  async function reload() {
    setError("");
    setConflict(false);
    try {
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Refresh failed.");
    }
  }
  // Release this tab's request, then show whatever request (possibly another
  // tab's) now occupies the slot. Removing a request that is no longer in the
  // slot is a no-op, so this never throws for a slot held by another request.
  async function releasePending(actionKey: string) {
    await removePending(queueKey, actionKey);
    setPending((await localValue<PendingAction>(queueKey)) ?? null);
  }
  async function sendPending(action: PendingAction) {
    setBusy(true);
    setError("");
    setNotice("");
    let committed = false;
    try {
      await savePending(queueKey, action); // durable before transmission
      setPending(action);
      await api(action.path, {
        method: "POST",
        body: JSON.stringify(action.payload),
        headers: { "Idempotency-Key": action.key },
        signal: AbortSignal.timeout(30000),
      });
      committed = true;
      // The server acknowledged this request. Clear a note draft it carried
      // before anything else, so the committed text is not submitted twice.
      if (
        action.path.endsWith("/notes") &&
        (action.payload as { text?: string }).text === note.trim()
      )
        setNote("");
      setConflict(false);
      setNotice(action.success);
      try {
        await releasePending(action.key);
      } catch {
        setPending(null);
        setError(
          "The action was saved, but this browser could not clear its local copy. Discard the saved request below if it remains.",
        );
      }
      await load().catch((e) =>
        setError(e instanceof Error ? e.message : "Refresh failed."),
      );
      return true;
    } catch (e) {
      if (committed) return true;
      setConflict(e instanceof ApiError && e.status === 409);
      if (e instanceof ApiError && [400, 404, 422].includes(e.status)) {
        await releasePending(action.key).catch(() => setPending(null));
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
  async function discardPending(action: PendingAction) {
    setBusy(true);
    try {
      await releasePending(action.key);
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "The saved request could not be discarded.",
      );
      return;
    } finally {
      setBusy(false);
    }
    await reload();
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
  async function reviewAction(
    values: Record<string, unknown> | Record<string, unknown>[],
  ) {
    if (!assessment || !review || !claim) return false;
    const isBatch = Array.isArray(values);
    const body = isBatch
      ? values.map((v) => ({
          ...v,
          expected_input_revision: claim.input_revision,
          expected_review_revision: review.review_revision,
        }))
      : {
          ...values,
          expected_input_revision: claim.input_revision,
          expected_review_revision: review.review_revision,
        };
    return mutate(
      base +
        "/assessments/" +
        assessment.assessment_revision +
        "/review-actions" +
        (isBatch ? "/batch" : ""),
      body,
      isBatch ? "Confirmations saved." : "Review action saved.",
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
  const row = assessment?.line_items.find((r) => r.entry_id === selectedRow);
  const correctedPages = assessment?.pages?.filter((p) => p.corrected_url) ?? [];

  // Categorize files into vehicle damage photos and estimate documents
  const photoFiles = files.filter(
    (f) =>
      f.role === "photograph" ||
      (!correctedPages.some((p) => p.file_id === f.file_id) &&
        f.media_type.startsWith("image/")),
  );
  const estimateFiles = files.filter(
    (f) =>
      f.role === "estimate" ||
      f.media_type === "application/pdf" ||
      correctedPages.some((p) => p.file_id === f.file_id),
  );

  const currentPage =
    correctedPages.find((p) => p.page_id === (row?.page_id ?? selectedPage)) ??
    correctedPages[0];
  const currentPhoto =
    photoFiles.find((f) => f.file_id === selectedFile) ?? photoFiles[0];
  const rawEstimateFile =
    estimateFiles.find((f) => f.file_id === currentPage?.file_id) ??
    estimateFiles[0];

  const page = correctedPages.find((p) => p.page_id === (row?.page_id ?? selectedPage));
  const activeEvidenceTab: "photos" | "pages" =
    evidenceTab === "photos" && photoFiles.length === 0 && (correctedPages.length > 0 || estimateFiles.length > 0)
      ? "pages"
      : evidenceTab === "pages" && correctedPages.length === 0 && estimateFiles.length === 0 && photoFiles.length > 0
      ? "photos"
      : evidenceTab;

  const file =
    activeEvidenceTab === "photos"
      ? currentPhoto
      : rawEstimateFile ?? files.find((f) => f.file_id === selectedFile) ?? files[0];
  const imageFromModels = assessment?.evidence_sources?.image === "real";
  const frozen = review?.finalized ?? false;
  // A shown assessment that is not the claim's current one is read-only.
  const notCurrent =
    !!assessment &&
    claim.assessment_revision !== assessment.assessment_revision;
  const saving = busy || conflict || !!pending || !localReady;
  const locked = saving || frozen || notCurrent;
  const latestRevision = claim.latest_assessment_revision ?? null;
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
            onClick={() => setShowEvidenceModal(true)}
            disabled={busy || frozen}
            title={frozen ? "Review finalized — claim evidence is frozen" : "Upload supplementary damage photographs or estimate pages"}
          >
            <UploadCloud size={15} /> Add Evidence
          </button>
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
          Field Surveyor <b>{claim.surveyor || "Unspecified"}</b>
        </span>
        <span>
          Reviewer <b>{review?.report?.header?.reviewed_by || claim.reviewer || "Acery AI"}</b>
        </span>
        <span>
          Input <b>{claim.input_revision}</b>
        </span>
        <span>
          Assessment{" "}
          <b>
            {claim.assessment_revision ??
              (claim.status === "processing" ? "Pending" : "None current")}
          </b>
          {notCurrent && (
            <> (showing {assessment!.assessment_revision}, not current)</>
          )}
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
          <strong>
            {!assessment ? "Processing status." : assessment.fixture_notice
              ? "Demonstration or mixed results." : "Model results."}
          </strong>{" "}
          {assessment ? (assessment.fixture_notice ??
            "Models processed the uploaded evidence. Document models have synthetic validation evidence only; review uncertain fields and proposed marks. Cost references are synthetic.") :
            "No completed results yet. Processing failures and missing evidence remain visible."}
        </p>
      </div>
      {error && <ErrorBanner message={error} />}
      {pollError && <ErrorBanner message={pollError} />}
      {processingWarning && (
        <div className="fixture-banner processing-warning" role="status">
          <Info size={18} />
          <p>{processingWarning}</p>
        </div>
      )}
      {notCurrent && (
        <section
          className="panel not-current-banner"
          aria-label="Assessment not current"
        >
          <h2>Assessment {assessment!.assessment_revision} is not current</h2>
          <p>
            It belongs to input revision {assessment!.input_revision}. Input
            revision {claim.input_revision} has no completed assessment
            {claim.status === "processing"
              ? " yet; processing is still running."
              : " because processing needs attention."}{" "}
            This view is read-only and cannot be finalized.
            {claim.status === "processing"
              ? ""
              : " Retry the failed stage to produce a current assessment."}
          </p>
          <button
            className="button button-outline button-small"
            onClick={() => setShowPrior(false)}
          >
            Hide earlier assessment
          </button>
        </section>
      )}
      {!!assessment?.invalidated_corrections?.length && (
        <div className="fixture-banner">
          <Info size={18} />
          <p>
            Earlier decisions need reconfirmation because their source evidence
            changed. The original decisions remain in the review history.
          </p>
        </div>
      )}
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
            onClick={() => void sendPending(pending)}
          >
            Retry saved request
          </button>
          <button
            className="button button-outline button-small"
            disabled={busy}
            onClick={() => void discardPending(pending)}
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
      {processing?.jobs.some((job) => job.retryable) && (
        <section className="panel" aria-label="Failed processing stages">
          <h2>Processing needs attention</h2>
          {processing.jobs
            .filter((job) => job.retryable)
            .map((job) => (
              <div className="job-state" key={job.job_key}>
                <span>
                  {label(job.stage ?? "Evidence processing")}:{" "}
                  {label(job.state)}
                </span>
                {job.error && <p>{job.error}</p>}
                <button
                  className="button button-outline button-small"
                  disabled={saving}
                  onClick={() =>
                    mutate(
                      `${base}/jobs/${encodeURIComponent(job.job_key)}/retry`,
                      {},
                      "Retry requested.",
                    )
                  }
                >
                  Retry {label(job.stage ?? "stage")}
                </button>
              </div>
            ))}
        </section>
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
              : "The worker prepares a versioned assessment. Results will appear here when processing completes."}
          </p>
          {processing?.jobs.map((job) => (
            <div className="job-state" key={job.job_key}>
              <span>{label(job.stage ?? "Evidence processing")}</span>
              <Status value={job.state} />
              {job.error && <p>{job.error}</p>}
            </div>
          ))}
          {claim.status === "awaiting_upload" && (
            <div style={{ display: "flex", gap: "10px", marginTop: "16px", justifyContent: "center" }}>
              <button
                className="button button-dark"
                onClick={() => setShowEvidenceModal(true)}
              >
                <UploadCloud size={16} /> Upload evidence to this claim
              </button>
              <Link className="button button-outline" to="/claims/new">
                New claim
              </Link>
            </div>
          )}
          {claim.assessment_revision === null && latestRevision !== null && (
            <div className="prior-assessment">
              <p>
                Assessment {latestRevision} was completed for an earlier input
                revision. It is not current and cannot be finalized.
              </p>
              <button
                className="button button-outline button-small"
                onClick={() => setShowPrior(true)}
              >
                Open assessment {latestRevision} read-only
              </button>
            </div>
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
                <div className="panel-heading" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px" }}>
                  <div style={{ display: "flex", gap: "12px", alignItems: "flex-start" }}>
                    <span className="section-icon">
                      <Camera size={20} />
                    </span>
                    <div>
                      <h2>Damage summary</h2>
                      <p>
                        Predicted damage is shown separately from part identity
                        and photograph coverage.
                      </p>
                    </div>
                  </div>
                </div>
                {assessment.damage_summary.length ? (
                  <DamageSummary
                    rows={assessment.damage_summary}
                    groups={assessment.part_summaries}
                    observations={assessment.observations}
                    files={assessment.files}
                    locked={locked}
                    onRetract={async (partCode, side, photoIds) => {
                      const validPhotos =
                        photoIds && photoIds.length > 0
                          ? photoIds
                          : assessment.review_photo_ids.length > 0
                          ? [assessment.review_photo_ids[0]]
                          : [];
                      if (!validPhotos.length) {
                        setError("No photographs available for this claim to anchor retraction.");
                        return;
                      }
                      await reviewAction({
                        action_type: "confirm_coverage",
                        part_code: partCode,
                        side: side,
                        photo_ids: validPhotos,
                        covers_enough: false,
                        reason_code: "surveyor_retracted",
                        note: "Confirmation retracted by surveyor",
                      });
                    }}
                    onConfirm={async (actions) => {
                      await reviewAction(actions);
                    }}
                    onPhoto={(photoId) => {
                      setSelectedRow(null);
                      setSelectedPage(null);
                      setSelectedFile(photoId);
                      setEvidenceTab("photos");
                      document.getElementById("evidence-panel")?.focus();
                    }}
                  />
                ) : (
                  <p className="muted">
                    No photographic assessment is available. Missing photographs
                    do not establish an unsupported repair.
                  </p>
                )}
                <IdentityControls
                  photoIds={assessment.review_photo_ids}
                  files={assessment.files}
                  uploadedPhotos={imageFromModels}
                  submit={reviewAction}
                  locked={locked}
                  existingCoverage={assessment.coverage ?? assessment.damage_summary}
                />
              </section>
              <section className="panel line-items-panel">
                <div className="panel-heading">
                  <span className="section-icon">
                    <FileText size={20} />
                  </span>
                  <div>
                    <h2>Estimate line items</h2>
                    <p>Amounts in {claim.currency}.</p>
                  </div>
                </div>
                {(() => {
                  const unlinkedMarks = assessment.marks.filter(
                    (m) => !m.entry_id && m.state !== "rejected",
                  );
                  if (!unlinkedMarks.length) return null;
                  return (
                    <div className="unlinked-marks-card">
                      <div className="unlinked-marks-header">
                        <AlertTriangle size={17} color="#d97706" />
                        <strong>Unlinked Pen Marks ({unlinkedMarks.length})</strong>
                        <span className="unlinked-marks-tip">
                          Detected pen strokes not automatically matched to a row. Link or reject them:
                        </span>
                      </div>
                      <div className="unlinked-marks-list">
                        {unlinkedMarks.map((mark) => (
                          <form
                            key={mark.mark_id}
                            className="unlinked-mark-row"
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
                            <div className="unlinked-mark-info">
                              <span className="unlinked-mark-kind">
                                {label(mark.type)}
                              </span>
                              <span className="unlinked-mark-reason">
                                {mark.reason}
                              </span>
                              {correctedPages.some((p) => p.page_id === mark.page_id) && (
                                <button
                                  type="button"
                                  className="button-link-small"
                                  onClick={() => {
                                    setSelectedRow(null);
                                    setSelectedPage(mark.page_id);
                                    setEvidenceTab("pages");
                                    document.getElementById("evidence-panel")?.focus();
                                  }}
                                >
                                  View mark on page
                                </button>
                              )}
                            </div>
                            <div className="unlinked-mark-actions">
                              <select
                                name="entry"
                                defaultValue=""
                                required
                                disabled={locked}
                                className="unlinked-mark-select"
                              >
                                <option value="" disabled>
                                  Assign mark to row...
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
                              <button
                                className="button button-dark button-small"
                                disabled={locked}
                              >
                                Link Row
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
                                  Reject
                                </button>
                              )}
                            </div>
                          </form>
                        ))}
                      </div>
                    </div>
                  );
                })()}
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
                          aria-haspopup="dialog"
                          onClick={() => {
                            setSelectedRow(item.entry_id);
                            setSelectedFile(item.evidence_ids[0] ?? null);
                            setEvidenceTab("pages");
                            setRowPopup(true);
                          }}
                        >
                          View evidence
                        </button>
                      </div>
                      <div className="amount-grid">
                        <div>
                          <span>
                            {item.printed_amount_corrected
                              ? "Corrected printed extraction"
                              : "Printed amount (current extraction)"}
                          </span>
                          {item.printed_amount_corrected && (
                            <small>
                              Original extraction:{" "}
                              {money(
                                item.original_printed_amount,
                                claim.currency,
                              )}
                              ; original text:{" "}
                              {item.original_amount_text ?? "Unreadable"}
                            </small>
                          )}
                          <strong>
                            {money(item.printed_amount, claim.currency)}
                          </strong>
                        </div>
                        <div>
                          <span>Effective surveyor amount</span>
                          <strong>
                            {item.row_state === "excluded" ? (
                              <span className="line-excluded-amount">Excluded ($0.00)</span>
                            ) : item.effective_amount === null ? (
                              "Withheld"
                            ) : (
                              money(item.effective_amount, claim.currency)
                            )}
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
                      <ResultDisplay item={item} />
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
                      {(() => {
                        const itemPage = assessment.pages?.find(
                          (p) => p.page_id === item.page_id,
                        );
                        const itemFile = itemPage
                          ? files.find((f) => f.file_id === itemPage.file_id)
                          : undefined;
                        const pageNumber = itemPage?.page_number;
                        const pageLabel = itemPage
                          ? `Estimate Page ${itemPage.page_number}${
                              itemFile ? ` (${itemFile.original_name})` : ""
                            }`
                          : undefined;
                        return (
                          <RowControls
                            row={item}
                            submit={reviewAction}
                            locked={locked}
                            dismissed={
                              assessment.review_overlay.dismissals[
                                item.finding_id
                              ]
                            }
                            pageNumber={pageNumber}
                            pageLabel={pageLabel}
                          />
                        );
                      })()}
                    </article>
                  ))
                ) : (
                  <p className="muted">
                    No estimate pages were supplied. There are no extracted rows
                    to review.
                  </p>
                )}
                {assessment.line_items.length > 0 && (() => {
                  const totalPrinted = assessment.line_items.reduce(
                    (sum, item) => sum + (Number(item.printed_amount) || 0),
                    0,
                  );
                  const agreed = agreedTotal(assessment.line_items, assessment.marks);
                  const totalAgreed = agreed.total;
                  const netDiff = totalAgreed === null ? 0 : totalAgreed - totalPrinted;
                  const pct =
                    totalPrinted > 0
                      ? ((netDiff / totalPrinted) * 100).toFixed(1)
                      : "0.0";
                  const excludedCount = assessment.line_items.filter(
                    (i) => i.row_state === "excluded",
                  ).length;
                  const priceChangedCount = assessment.line_items.filter(
                    (i) => i.effective_price_source === "surveyor_entry",
                  ).length;

                  return (
                    <div className="review-estimate-summary-bar">
                      <div className="summary-bar-header">
                        <div className="summary-bar-title-wrap">
                          <Calculator size={19} className="summary-bar-icon" />
                          <div>
                            <h4>Estimate &amp; Agreed Totals</h4>
                            <span className="summary-bar-sub">
                              Live calculation across {assessment.line_items.length} line items
                              {excludedCount > 0 && ` (${excludedCount} excluded)`}
                              {priceChangedCount > 0 && ` (${priceChangedCount} revised)`}
                            </span>
                          </div>
                        </div>
                        {totalAgreed === null ? (
                          <div className="summary-variance-pill neutral">
                            <span>Net Adjustment:</span>
                            <strong>Pending</strong>
                          </div>
                        ) : Math.abs(netDiff) >= 0.01 ? (
                          <div
                            className={`summary-variance-pill ${
                              netDiff < 0 ? "negative" : "positive"
                            }`}
                          >
                            <span>Net Adjustment:</span>
                            <strong>
                              {netDiff < 0 ? "-" : "+"}
                              {money(Math.abs(netDiff), claim.currency)} ({pct}%)
                            </strong>
                          </div>
                        ) : (
                          <div className="summary-variance-pill neutral">
                            <span>Net Adjustment:</span>
                            <strong>$0.00 (Agreed in full)</strong>
                          </div>
                        )}
                      </div>
                      <div className="summary-bar-metrics">
                        <div className="summary-metric-card">
                          <span className="metric-label">Declared Estimate</span>
                          <strong className="metric-value declared">
                            {money(totalPrinted, claim.currency)}
                          </strong>
                          <span className="metric-sub">Sum of printed amounts</span>
                        </div>
                        <div className="summary-metric-arrow">
                          <ArrowRight size={20} />
                        </div>
                        <div className="summary-metric-card highlight">
                          <span className="metric-label">Effective Agreed Total</span>
                          <strong className="metric-value agreed">
                            {totalAgreed === null ? "Pending" : money(totalAgreed, claim.currency)}
                          </strong>
                          <span className="metric-sub">
                            {totalAgreed === null
                              ? undecidedText(agreed)
                              : "Single-part basis · Pre-tax"}
                          </span>
                        </div>
                      </div>
                    </div>
                  );
                })()}
                <div
                  className="completeness-signoff-wrapper"
                  id="declaration-completeness-section"
                >
                  <div className="completeness-signoff-heading">
                    <span className="signoff-step-tag">ESTIMATE REVIEW SIGN-OFF</span>
                    <h3>Declaration Completeness</h3>
                    <p>
                      Now that you have reviewed the extracted line items above, confirm whether all estimate pages and items are present.
                    </p>
                  </div>
                  <CompletenessControl
                    declaration={assessment.declaration}
                    completenessState={assessment.declaration_completeness}
                    submit={reviewAction}
                    locked={locked}
                  />
                </div>
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
                    <h2>
                      Review notes
                      {review?.actions && review.actions.length > 0 && (
                        <span className="review-notes-count-badge">
                          {review.actions.length}
                        </span>
                      )}
                    </h2>
                    <p>Saved with the actor, time and review revision.</p>
                  </div>
                </div>
                {review?.actions.length ? (
                  <>
                    {review.actions.length > 3 && (
                      <div className="review-history-toggle-bar">
                        <span className="muted" style={{ fontSize: "11.5px" }}>
                          {expandReviewHistory
                            ? `Showing all ${review.actions.length} recorded actions`
                            : `Showing latest 3 of ${review.actions.length} actions`}
                        </span>
                        <button
                          type="button"
                          className="review-history-toggle-btn"
                          onClick={() => setExpandReviewHistory((prev) => !prev)}
                        >
                          {expandReviewHistory ? (
                            <>
                              <ChevronUp size={14} /> Show recent only
                            </>
                          ) : (
                            <>
                              <ChevronDown size={14} /> Show earlier history (
                              {review.actions.length - 3} more)
                            </>
                          )}
                        </button>
                      </div>
                    )}
                    <ol
                      className={`review-history ${
                        expandReviewHistory ? "review-history-expanded" : ""
                      }`}
                    >
                      {(expandReviewHistory
                        ? review.actions
                        : review.actions.slice(-3)
                      ).map((action) => (
                        <li key={action.action_id}>
                          <strong>{action.actor}</strong>
                          <time>
                            {new Date(action.created_at).toLocaleString()}
                          </time>
                          <p>{actionText(action)}</p>
                        </li>
                      ))}
                    </ol>
                  </>
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
              <div style={{ marginBottom: "14px" }}>
                <button
                  type="button"
                  className="button button-outline button-small"
                  style={{ width: "100%", display: "flex", justifyContent: "center", alignItems: "center", gap: "6px" }}
                  onClick={() => setShowEvidenceModal(true)}
                  disabled={busy || frozen}
                  title={frozen ? "Review finalized — claim evidence is frozen" : "Upload supplementary evidence"}
                >
                  <UploadCloud size={15} /> Upload supplementary evidence
                </button>
                {frozen && (
                  <div className="frozen-upload-notice">
                    <ShieldCheck size={13} color="#059669" />
                    <span>Review finalized. Original evidence is frozen.</span>
                  </div>
                )}
              </div>
              <div className="evidence-tabs">
                <button
                  type="button"
                  className={`evidence-tab ${activeEvidenceTab === "photos" ? "active" : ""}`}
                  onClick={() => {
                    setSelectedRow(null);
                    setEvidenceTab("photos");
                  }}
                  disabled={photoFiles.length === 0}
                  title={
                    photoFiles.length === 0
                      ? "No damage photographs uploaded"
                      : "View vehicle damage photographs"
                  }
                >
                  <Camera size={14} />
                  <span>Damage Photos ({photoFiles.length})</span>
                </button>
                <button
                  type="button"
                  className={`evidence-tab ${activeEvidenceTab === "pages" ? "active" : ""}`}
                  onClick={() => {
                    setEvidenceTab("pages");
                  }}
                  disabled={correctedPages.length === 0 && estimateFiles.length === 0}
                  title={
                    correctedPages.length === 0 && estimateFiles.length === 0
                      ? "No estimate documents uploaded"
                      : "View estimate pages and AI bounding boxes"
                  }
                >
                  <FileText size={14} />
                  <span>Estimate Pages ({correctedPages.length || estimateFiles.length})</span>
                </button>
              </div>

              {activeEvidenceTab === "photos" && (
                <>
                  {photoFiles.length > 0 ? (
                    <>
                      <label className="evidence-select">
                        Damage photograph
                        <select
                          value={currentPhoto?.file_id ?? ""}
                          onChange={(e) => {
                            setSelectedRow(null);
                            setSelectedPage(null);
                            setSelectedFile(e.target.value);
                          }}
                        >
                          {photoFiles.map((f, i) => (
                            <option value={f.file_id} key={f.file_id}>
                              {f.original_name || `Damage Photo ${i + 1}`}
                            </option>
                          ))}
                        </select>
                      </label>
                      {currentPhoto && (
                        <>
                          <div
                            className="evidence-preview-wrap clickable"
                            onClick={() => setPopupEvidence({ kind: "file", file: currentPhoto })}
                            title="Click to inspect full-size photo in popup"
                          >
                            <img
                              src={currentPhoto.url}
                              alt={`Damage photo: ${currentPhoto.original_name}`}
                              onError={(e) => {
                                e.currentTarget.style.display = "none";
                                setError(
                                  "The original image could not be loaded. Use the original-file link or refresh.",
                                );
                              }}
                            />
                          </div>
                          <button
                            type="button"
                            className="button button-outline button-small"
                            style={{
                              width: "100%",
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "center",
                              gap: "6px",
                              marginTop: "10px",
                            }}
                            onClick={() => setPopupEvidence({ kind: "file", file: currentPhoto })}
                          >
                            <Maximize2 size={14} /> Inspect full-size photo
                          </button>
                          <p className="evidence-caption">{currentPhoto.original_name}</p>
                        </>
                      )}
                    </>
                  ) : (
                    <div className="evidence-empty">
                      <Camera size={28} />
                      <h3>No damage photos</h3>
                      <p>No vehicle damage photographs have been uploaded for this claim.</p>
                    </div>
                  )}
                </>
              )}

              {activeEvidenceTab === "pages" && (
                <>
                  {correctedPages.length > 0 ? (
                    <>
                      <label className="evidence-select">
                        Estimate page
                        <select
                          value={currentPage?.page_id ?? ""}
                          onChange={(e) => {
                            setSelectedRow(null);
                            setSelectedPage(e.target.value || null);
                          }}
                        >
                          {correctedPages.map((p) => {
                            const pFile = files.find((f) => f.file_id === p.file_id);
                            return (
                              <option key={p.page_id} value={p.page_id}>
                                {pFile?.original_name ?? "Estimate"} · page {p.page_number}
                              </option>
                            );
                          })}
                        </select>
                      </label>
                      {currentPage?.corrected_url && (
                        <div
                          className="evidence-preview-wrap clickable"
                          onClick={() => {
                            const pFile = files.find((f) => f.file_id === currentPage.file_id);
                            const pTitle = `${pFile?.original_name ?? "Estimate"} · Page ${currentPage.page_number}`;
                            setPopupEvidence({ kind: "page", page: currentPage, name: pTitle });
                          }}
                          title="Click to inspect original document page"
                        >
                          <img
                            src={currentPage.corrected_url}
                            alt={`Estimate page ${currentPage.page_number}`}
                            style={{ display: "block", width: "100%", borderRadius: "4px" }}
                          />
                        </div>
                      )}
                      <p className="evidence-caption">
                        Original estimate document page without model annotations.
                      </p>
                      <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginTop: "10px" }}>
                        <button
                          type="button"
                          className="button button-outline button-small"
                          style={{
                            width: "100%",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            gap: "6px",
                          }}
                          onClick={() => {
                            const pFile = files.find((f) => f.file_id === currentPage.file_id);
                            const pTitle = `${pFile?.original_name ?? "Estimate"} · Page ${currentPage.page_number}`;
                            setPopupEvidence({
                              kind: "page",
                              page: currentPage,
                              name: pTitle,
                            });
                          }}
                        >
                          <Maximize2 size={14} /> Inspect full-size document
                        </button>
                        {rawEstimateFile && (
                          <button
                            type="button"
                            className="button button-link-small"
                            style={{ textAlign: "center", color: "#64748b", fontSize: "12px" }}
                            onClick={() => setPopupEvidence({ kind: "file", file: rawEstimateFile })}
                          >
                            Inspect raw original PDF ({rawEstimateFile.original_name})
                          </button>
                        )}
                      </div>
                    </>
                  ) : estimateFiles.length > 0 ? (
                    <>
                      <label className="evidence-select">
                        Original estimate document
                        <select
                          value={rawEstimateFile?.file_id ?? ""}
                          onChange={(e) => {
                            setSelectedRow(null);
                            setSelectedPage(null);
                            setSelectedFile(e.target.value);
                          }}
                        >
                          {estimateFiles.map((f) => (
                            <option value={f.file_id} key={f.file_id}>
                              {f.original_name}
                            </option>
                          ))}
                        </select>
                      </label>
                      {rawEstimateFile && (
                        <>
                          <div className="evidence-preview">
                            {rawEstimateFile.media_type === "application/pdf" ? (
                              <iframe
                                title={`Original estimate: ${rawEstimateFile.original_name}`}
                                src={rawEstimateFile.url}
                              />
                            ) : (
                              <img
                                src={rawEstimateFile.url}
                                alt={`Original evidence: ${rawEstimateFile.original_name}`}
                              />
                            )}
                          </div>
                          <button
                            type="button"
                            className="button button-outline button-small"
                            style={{
                              width: "100%",
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "center",
                              gap: "6px",
                            }}
                            onClick={() => setPopupEvidence({ kind: "file", file: rawEstimateFile })}
                          >
                            <Maximize2 size={14} /> Inspect original document
                          </button>
                          <p className="evidence-caption">{rawEstimateFile.original_name}</p>
                        </>
                      )}
                    </>
                  ) : (
                    <div className="evidence-empty">
                      <FileText size={28} />
                      <h3>No estimate documents</h3>
                      <p>No estimate documents have been uploaded for this claim.</p>
                    </div>
                  )}
                </>
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
                  {correctedPages.length > 0
                    ? "Document overlays show model proposals. Confirm marks and review uncertain fields before finalizing."
                    : imageFromModels
                    ? "The part and damage masks are stored but not shown here. Repair prices are not validated."
                    : "Mock processing does not produce evidence overlays or validate repair prices."}
                </span>
              </div>
            </aside>
            {rowPopup && row && (
              <EvidenceDialog
                title={row.description}
                subtitle={
                  page
                    ? `${files.find((f) => f.file_id === page.file_id)?.original_name ?? "Estimate"} \u00b7 page ${page.page_number}`
                    : file?.original_name
                }
                onClose={() => setRowPopup(false)}
              >
                {page?.corrected_url ? (
                  <>
                    <CorrectedPageView page={page} rows={assessment.line_items}
                      marks={assessment.marks} selectedRow={selectedRow} centerOnSelected />
                    <p className="evidence-dialog-note">
                      Blue: extracted rows. Red: proposed or confirmed marks. Grey: rejected marks.
                      The selected row is shaded.
                    </p>
                    <div
                      style={{
                        marginTop: "14px",
                        display: "flex",
                        justifyContent: "flex-end",
                        alignItems: "center",
                        gap: "10px",
                        flexWrap: "wrap",
                      }}
                    >
                      <a
                        className="button button-outline button-small"
                        href={page.corrected_url}
                        target="_blank"
                        rel="noreferrer"
                        style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
                      >
                        <ExternalLink size={14} /> Open in new browser tab
                      </a>
                      {(() => {
                        if (rawEstimateFile) {
                          const isPdf =
                            rawEstimateFile.media_type === "application/pdf" ||
                            rawEstimateFile.original_name.toLowerCase().endsWith(".pdf");
                          return (
                            <a
                              className="button button-small"
                              href={`${rawEstimateFile.url}?download=1`}
                              download={rawEstimateFile.original_name}
                              style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
                            >
                              <Download size={14} /> Download {isPdf ? "PDF" : "document"}
                            </a>
                          );
                        }
                        return (
                          <a
                            className="button button-small"
                            href={`${page.corrected_url}?download=1`}
                            download={`estimate_page_${page.page_id}.png`}
                            style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
                          >
                            <Download size={14} /> Download page
                          </a>
                        );
                      })()}
                    </div>
                  </>
                ) : file ? (
                  <>
                    <EvidenceFile file={file} />
                    <div
                      style={{
                        marginTop: "14px",
                        display: "flex",
                        justifyContent: "flex-end",
                        alignItems: "center",
                        gap: "10px",
                        flexWrap: "wrap",
                      }}
                    >
                      <a
                        className="button button-outline button-small"
                        href={file.url}
                        target="_blank"
                        rel="noreferrer"
                        style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
                      >
                        <ExternalLink size={14} /> Open in new browser tab
                      </a>
                      {(() => {
                        const isPdf =
                          file.media_type === "application/pdf" ||
                          file.original_name.toLowerCase().endsWith(".pdf");
                        return (
                          <a
                            className="button button-small"
                            href={`${file.url}?download=1`}
                            download={file.original_name}
                            style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
                          >
                            <Download size={14} /> Download{" "}
                            {isPdf
                              ? "PDF"
                              : file.role === "photograph" ||
                                file.media_type.startsWith("image/")
                              ? "photo"
                              : "document"}
                          </a>
                        );
                      })()}
                    </div>
                  </>
                ) : (
                  <p className="evidence-dialog-note">
                    No original file is linked to this row, so there is nothing to enlarge. The
                    Evidence panel lets you browse the uploaded files separately.
                  </p>
                )}
              </EvidenceDialog>
            )}
            {popupEvidence && (
              <EvidenceDialog
                title={
                  popupEvidence.kind === "file"
                    ? popupEvidence.file.original_name
                    : popupEvidence.name
                }
                subtitle={
                  popupEvidence.kind === "file"
                    ? popupEvidence.file.role === "photograph" ||
                      popupEvidence.file.media_type.startsWith("image/")
                      ? "Vehicle Photograph · Original Evidence"
                      : "Estimate Document · Original Evidence"
                    : `Page ${popupEvidence.page.page_number} · Original Document`
                }
                onClose={() => setPopupEvidence(null)}
              >
                {popupEvidence.kind === "file" ? (
                  <>
                    <EvidenceFile file={popupEvidence.file} />
                    <div
                      style={{
                        marginTop: "14px",
                        display: "flex",
                        justifyContent: "flex-end",
                        alignItems: "center",
                        gap: "10px",
                        flexWrap: "wrap",
                      }}
                    >
                      <a
                        className="button button-outline button-small"
                        href={popupEvidence.file.url}
                        target="_blank"
                        rel="noreferrer"
                        style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
                      >
                        <ExternalLink size={14} /> Open in new browser tab
                      </a>
                      {(() => {
                        const isPdf =
                          popupEvidence.file.media_type === "application/pdf" ||
                          popupEvidence.file.original_name.toLowerCase().endsWith(".pdf");
                        return (
                          <a
                            className="button button-small"
                            href={`${popupEvidence.file.url}?download=1`}
                            download={popupEvidence.file.original_name}
                            style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
                          >
                            <Download size={14} /> Download{" "}
                            {isPdf
                              ? "PDF"
                              : popupEvidence.file.role === "photograph" ||
                                popupEvidence.file.media_type.startsWith("image/")
                              ? "photo"
                              : "document"}
                          </a>
                        );
                      })()}
                    </div>
                  </>
                ) : (
                  <>
                    {popupEvidence.page.corrected_url ? (
                      <EvidenceImage
                        url={popupEvidence.page.corrected_url}
                        alt={`Estimate page ${popupEvidence.page.page_number}`}
                      />
                    ) : null}
                    <div
                      style={{
                        marginTop: "14px",
                        display: "flex",
                        justifyContent: "flex-end",
                        alignItems: "center",
                        gap: "10px",
                        flexWrap: "wrap",
                      }}
                    >
                      <a
                        className="button button-outline button-small"
                        href={rawEstimateFile?.url || popupEvidence.page.corrected_url || "#"}
                        target="_blank"
                        rel="noreferrer"
                        style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
                      >
                        <ExternalLink size={14} /> Open in new browser tab
                      </a>
                      {(() => {
                        if (rawEstimateFile) {
                          const isPdf =
                            rawEstimateFile.media_type === "application/pdf" ||
                            rawEstimateFile.original_name.toLowerCase().endsWith(".pdf");
                          return (
                            <a
                              className="button button-small"
                              href={`${rawEstimateFile.url}?download=1`}
                              download={rawEstimateFile.original_name}
                              style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
                            >
                              <Download size={14} /> Download {isPdf ? "PDF" : "document"}
                            </a>
                          );
                        }
                        if (popupEvidence.page.corrected_url) {
                          return (
                            <a
                              className="button button-small"
                              href={`${popupEvidence.page.corrected_url}?download=1`}
                              download={`estimate_page_${popupEvidence.page.page_id}.png`}
                              style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
                            >
                              <Download size={14} /> Download image
                            </a>
                          );
                        }
                        return null;
                      })()}
                    </div>
                  </>
                )}
              </EvidenceDialog>
            )}
          </div>
          <section className="panel finalize-panel">
            <div>
              <h2>
                {frozen
                  ? "Review finalized"
                  : notCurrent
                    ? "Not finalizable"
                    : "Ready to finalize?"}
              </h2>
              <p>
                {frozen
                  ? "This review is frozen. The report uses exactly this assessment and review revision."
                  : notCurrent
                    ? "This assessment is not current. Only the current input revision's completed assessment can be finalized."
                    : "Freeze this completed assessment and review for the printable report."}
              </p>
              {blockers.length > 0 && !frozen && !notCurrent && (
                <ul className="blocker-list">
                  {blockers.map((check) => {
                    const isCompleteness =
                      check.code.includes("completeness") ||
                      check.message.toLowerCase().includes("completeness");
                    return (
                      <li key={check.code}>
                        <div className="blocker-item-row">
                          <span>{check.message}</span>
                          {isCompleteness && (
                            <button
                              type="button"
                              className="blocker-action-link"
                              onClick={() => {
                                const el = document.getElementById("declaration-completeness-section");
                                if (el) {
                                  el.scrollIntoView({ behavior: "smooth", block: "center" });
                                  el.classList.add("highlight-pulse");
                                  setTimeout(() => el.classList.remove("highlight-pulse"), 2500);
                                }
                              }}
                            >
                              Go to Declaration Completeness &rarr;
                            </button>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
              <small>
                More information needed remains visible in the report. Final
                claim approval is not recorded.
              </small>
            </div>
            {frozen ? (
              <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" }}>
                <Link
                  className="button button-dark"
                  to={`${base}/print?assessment=${assessment.assessment_revision}&review=${review!.review_revision}`}
                >
                  <Printer size={17} /> Print report
                </Link>
                <button
                  type="button"
                  className="button button-outline button-supplementary-reopen"
                  onClick={() => setShowReopeningModal(true)}
                  title="File supplementary post-dismantling damage or additional estimate pages"
                >
                  <UploadCloud size={17} /> File Supplementary Claim
                </button>
              </div>
            ) : notCurrent ? null : (
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
      {showReopeningModal && claim && review && assessment && (
        <SupplementaryReopeningModal
          claim={claim}
          assessmentRevision={assessment.assessment_revision}
          reviewRevision={review.review_revision}
          inputRevision={claim.input_revision || assessment.input_revision || 0}
          onClose={() => setShowReopeningModal(false)}
          onProceed={() => {
            setShowReopeningModal(false);
            setShowEvidenceModal(true);
          }}
        />
      )}
      {showEvidenceModal && claim && (
        <SupplementaryEvidenceModal
          claim={claim}
          onClose={() => setShowEvidenceModal(false)}
          onSuccess={async () => {
            setNotice(`Amended claim ${claim.reference}. The pipeline is evaluating the new evidence revision.`);
            await load();
          }}
        />
      )}
    </>
  );
}

function cleanPinnedVersions(
  versions: Record<string, string> = {},
): [string, string][] {
  const keyMap: Record<string, string> = {
    "parts.parts_model": "Parts model",
    "damage.damage_model": "Damage model",
    "page_read.ocr": "OCR engine",
    "page_read.ocr_engine": "OCR engine",
    "line_items.line_items_model": "Line items model",
    "pen_marks.penmark_model": "Pen marks model",
    cost_table: "Cost table",
    rules_config: "Rules config",
    "damage.taxonomy": "Damage taxonomy",
    "parts.taxonomy": "Parts taxonomy",
  };

  const seenLabels = new Set<string>();
  const pairs: [string, string][] = [];

  for (const [k, label] of Object.entries(keyMap)) {
    if (versions[k] && !seenLabels.has(label)) {
      seenLabels.add(label);
      const val = versions[k].includes("/")
        ? versions[k].split("/").pop()!
        : versions[k];
      pairs.push([label, val]);
    }
  }

  for (const [k, v] of Object.entries(versions)) {
    if (
      !k.includes("sha256") &&
      !k.endsWith(".code") &&
      !k.endsWith("_config") &&
      !/^[a-f0-9]{32,64}$/i.test(v) &&
      !Object.keys(keyMap).includes(k) &&
      !["cost_basis", "cost_reference", "reason_catalogue"].includes(k)
    ) {
      const label = k.replace(/^[a-z_]+\./, "").replace(/_/g, " ");
      const capitalized = label.charAt(0).toUpperCase() + label.slice(1);
      if (!seenLabels.has(capitalized)) {
        seenLabels.add(capitalized);
        pairs.push([capitalized, v]);
      }
    }
  }

  return pairs;
}

function formatPinnedVersions(versions: Record<string, string> = {}): string {
  return cleanPinnedVersions(versions)
    .map(([k, v]) => `${k}: ${v}`)
    .join(" / ");
}

interface PartSummaryEntry {
  partCode: string;
  side: string;
  displayName: string;
  lineItems: LineItem[];
  damageCodes: string[];
  photoCount: number;
  coverageState: string;
  coverageReasons: string[];
  approvalStatus: "approved" | "rejected" | "action_required" | "unclaimed" | "not_evaluated";
  statusText: string;
}

function computePartCategorySummaries(
  damageRows: DamageRow[] = [],
  lineItems: LineItem[] = [],
  coverageList: { part_code: string; side: string; state: string; reasons?: string[]; covering_photo_ids?: string[] }[] = []
): PartSummaryEntry[] {
  const map = new Map<string, PartSummaryEntry>();

  for (const item of lineItems) {
    const pCode = item.part_code || item.description || "unspecified";
    const side = item.side || "unknown";
    const key = `${pCode}::${side}`;
    if (!map.has(key)) {
      map.set(key, {
        partCode: pCode,
        side,
        displayName: label(pCode),
        lineItems: [],
        damageCodes: [],
        photoCount: 0,
        coverageState: "not_photographed",
        coverageReasons: [],
        approvalStatus: "not_evaluated",
        statusText: "Pending evaluation",
      });
    }
    map.get(key)!.lineItems.push(item);
  }

  for (const row of damageRows) {
    const pCode = row.part_code || "unresolved";
    const side = row.side || "unknown";
    const key = `${pCode}::${side}`;
    let entry = map.get(key);
    if (!entry) {
      const altKey = Array.from(map.keys()).find((k) => k.startsWith(`${pCode}::`));
      if (altKey) entry = map.get(altKey);
    }
    if (!entry) {
      entry = {
        partCode: pCode,
        side,
        displayName: label(pCode),
        lineItems: [],
        damageCodes: [],
        photoCount: 0,
        coverageState: row.coverage || "unresolved",
        coverageReasons: [],
        approvalStatus: "unclaimed",
        statusText: "Unclaimed damage",
      };
      map.set(key, entry);
    }
    if (row.damage_codes?.length) {
      entry.damageCodes = Array.from(new Set([...entry.damageCodes, ...row.damage_codes]));
    }
    entry.photoCount = Math.max(entry.photoCount, row.photo_count || 0);
    if (row.coverage && row.coverage !== "unresolved") {
      entry.coverageState = row.coverage;
    }
    if (row.reason_codes?.length) {
      entry.coverageReasons = Array.from(new Set([...entry.coverageReasons, ...row.reason_codes]));
    }
  }

  for (const cov of coverageList) {
    const key = `${cov.part_code}::${cov.side}`;
    let entry = map.get(key);
    if (!entry) {
      const altKey = Array.from(map.keys()).find((k) => k.startsWith(`${cov.part_code}::`));
      if (altKey) entry = map.get(altKey);
    }
    if (entry) {
      if (cov.state && (entry.coverageState === "not_photographed" || entry.coverageState === "unresolved")) {
        entry.coverageState = cov.state;
      }
      if (cov.reasons?.length) {
        entry.coverageReasons = Array.from(new Set([...entry.coverageReasons, ...cov.reasons]));
      }
      if (cov.covering_photo_ids?.length) {
        entry.photoCount = Math.max(entry.photoCount, cov.covering_photo_ids.length);
      }
    }
  }

  for (const entry of map.values()) {
    if (entry.lineItems.length > 0) {
      const results = entry.lineItems.map((i) => i.overall_result);
      if (results.includes("ok")) {
        entry.approvalStatus = "approved";
        entry.statusText = "Approved / Supported";
      } else if (results.includes("unsupported")) {
        entry.approvalStatus = "rejected";
        entry.statusText = "Rejected (Unsupported)";
      } else if (results.includes("insufficient_evidence")) {
        entry.approvalStatus = "action_required";
        entry.statusText = "Action Required (Missing Evidence)";
      } else if (results.includes("cost_outlier")) {
        entry.approvalStatus = "rejected";
        entry.statusText = "Cost Outlier";
      } else {
        entry.approvalStatus = "not_evaluated";
        entry.statusText = "Not Evaluated";
      }
    } else {
      if (entry.damageCodes.length > 0) {
        entry.approvalStatus = "unclaimed";
        entry.statusText = "Unclaimed Damage (Potential Addition)";
      } else {
        entry.approvalStatus = "not_evaluated";
        entry.statusText = "Intact (No Claim)";
      }
    }
  }

  return Array.from(map.values());
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
  const meaningfulVersions = cleanPinnedVersions(assessment.versions);
  const footerText = [
    claim.reference,
    "Input " + assessment.input_revision,
    "Assessment " + assessment.assessment_revision,
    "Review " + review.review_revision,
    assessment.evidence_sources?.image === "real"
      ? "Model results for photographs; demonstration fixtures for documents. Reference costs are synthetic."
      : "Demonstration fixtures. Reference costs are synthetic.",
    ...meaningfulVersions.map(([key, value]) => key + ": " + value),
  ].join(" / ");
  const footerCssString = JSON.stringify(footerText);
  const partSummaries = computePartCategorySummaries(
    assessment.damage_summary,
    assessment.line_items,
    assessment.coverage,
  );
  const totalPrintedAmount = assessment.line_items.reduce(
    (sum, item) => sum + (Number(item.printed_amount) || 0),
    0,
  );
  const effectiveTotal = agreedTotal(assessment.line_items, assessment.marks);
  return (
    <div className="print-report">
      <style>
        {"@media print { @page { @bottom-center { content: " +
          footerCssString +
          "; font-family: Arial, sans-serif; font-size: 6.5pt; line-height: 1.25; text-align: left; vertical-align: top; padding-top: 3mm; white-space: normal; overflow-wrap: anywhere; } " +
          '@top-right { content: "Page " counter(page) " of " counter(pages); font-family: Arial, sans-serif; font-size: 8pt; color: #555; } } }'}
      </style>
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
          <dt>Field Surveyor (Report)</dt>
          <dd>{claim.surveyor || "Not supplied"}</dd>
          <dt>Reviewing Surveyor</dt>
          <dd>{review.report?.header?.reviewed_by || claim.reviewer || "Acery AI"}</dd>
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
          <dt>Revisions</dt>
          <dd className="report-revisions">
            Input {assessment.input_revision} &middot; Assessment{" "}
            {assessment.assessment_revision} &middot; Review{" "}
            {review.review_revision}
          </dd>
          <dt>Pinned versions</dt>
          <dd className="report-pinned-versions">
            {formatPinnedVersions(assessment.versions)}
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
                <td>
                  {money(item.printed_amount, claim.currency)}
                  {item.printed_amount_corrected && (
                    <small>
                      Corrected. Original extraction:{" "}
                      {money(item.original_printed_amount, claim.currency)};
                      text: {item.original_amount_text ?? "Unreadable"}
                    </small>
                  )}
                </td>
                <td>
                  {item.row_state === "excluded" ? (
                    <span className="line-excluded-amount">Excluded ($0.00)</span>
                  ) : (
                    money(item.effective_amount, claim.currency)
                  )}
                </td>
                <td>
                  <strong>{resultLabel(item)}</strong>
                  <small>{item.reason}</small>
                </td>
              </tr>
            ))}
          </tbody>
          {assessment.line_items.length > 0 && (
            <tfoot>
              <tr className="report-table-total">
                <td>
                  <strong>Total</strong>
                  <small>
                    {
                      assessment.line_items.filter(
                        (i) => i.overall_result === "ok",
                      ).length
                    }{" "}
                    of {assessment.line_items.length} items supported
                  </small>
                </td>
                <td>
                  <strong>{money(totalPrintedAmount, claim.currency)}</strong>
                </td>
                <td>
                  {effectiveTotal.total === null ? (
                    <>
                      <strong>Pending</strong>
                      <small>{undecidedText(effectiveTotal)}</small>
                    </>
                  ) : (
                    <strong>{money(effectiveTotal.total, claim.currency)}</strong>
                  )}
                </td>
                <td>
                  <small>Single-part basis &middot; Pre-tax</small>
                </td>
              </tr>
            </tfoot>
          )}
        </table>
        {!assessment.line_items.length && <p>No estimate rows available.</p>}
        <h2>Photographic coverage</h2>
        <p className="report-basis">
          Summary of evaluated vehicle part categories, photographic coverage, and
          review determinations.
        </p>
        <table className="report-table" style={{ marginBottom: "24px" }}>
          <thead>
            <tr>
              <th>Part Category</th>
              <th>Photographic Coverage</th>
              <th>Observed Damage</th>
              <th>Review Status</th>
            </tr>
          </thead>
          <tbody>
            {partSummaries.map((p) => {
              const isMissing =
                p.coverageState === "not_photographed" ||
                p.coverageState === "unresolved" ||
                p.coverageState === "inadequate" ||
                p.photoCount === 0 ||
                p.approvalStatus === "action_required";
              return (
                <tr
                  key={`${p.partCode}-${p.side}`}
                  className={isMissing ? "row-missing-evidence" : undefined}
                >
                  <td>
                    <strong>{p.displayName}</strong>
                    <small>
                      Side: {p.side !== "unknown" ? label(p.side) : "Not established"}
                    </small>
                  </td>
                  <td>
                    {isMissing ? (
                      <span className="report-badge report-badge-missing">
                        Missing Evidence ({p.photoCount} photos)
                      </span>
                    ) : (
                      <span className="report-badge report-badge-approved">
                        Adequate ({p.photoCount} photos)
                      </span>
                    )}
                    {p.coverageReasons.length > 0 && (
                      <small>
                        {p.coverageReasons.map(reasonText).join("; ")}
                      </small>
                    )}
                  </td>
                  <td>
                    {p.damageCodes.length > 0 ? (
                      <strong>{p.damageCodes.map(label).join(", ")}</strong>
                    ) : (
                      <span className="muted">No damage detected</span>
                    )}
                  </td>
                  <td>
                    <span
                      className={`report-badge report-badge-${
                        p.approvalStatus === "approved"
                          ? "approved"
                          : p.approvalStatus === "rejected"
                            ? "rejected"
                            : p.approvalStatus === "action_required"
                              ? "missing"
                              : p.approvalStatus === "unclaimed"
                                ? "unclaimed"
                                : "neutral"
                      }`}
                    >
                      {p.statusText}
                    </span>
                    {p.lineItems.length > 0 && (
                      <small>
                        Claimed: {p.lineItems.map((i) => i.operation).join(", ")} &middot;{" "}
                        {(() => {
                          const partTotal = agreedTotal(p.lineItems).total;
                          return partTotal === null
                            ? "amount pending"
                            : money(partTotal, claim.currency);
                        })()}
                      </small>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <DamageSummary
          rows={assessment.damage_summary}
          groups={assessment.part_summaries}
          observations={assessment.observations}
          files={assessment.files}
          print
        />
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
        ].length === 0 ? (
          <p className="muted">None</p>
        ) : (
          [
            ...(snapshot.report?.open_additions ?? []),
            ...(snapshot.report?.withheld_additions ?? []),
          ].map((a) => (
            <p key={a.candidate_id}>
              {a.part_code ?? "Unresolved"} / {a.side}: {a.reason.text}
            </p>
          ))
        )}
        <h2>Pen-mark decisions</h2>
        {!snapshot.report?.mark_decisions?.length ? (
          <p className="muted">None</p>
        ) : (
          snapshot.report.mark_decisions.map((m) => (
            <p key={m.mark_id}>
              {label(m.mark_type)}: {m.state}, {label(m.origin)}.{" "}
              {m.confirmed_amount !== null
                ? money(m.confirmed_amount, claim.currency)
                : ""}{" "}
              {m.decided_by ?? "No decision recorded"}.
            </p>
          ))
        )}
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
          <p>{formatPinnedVersions(assessment.versions)}</p>
        </div>
      </article>
    </div>
  );
}
