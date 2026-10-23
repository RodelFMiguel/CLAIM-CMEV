import { useEffect, useState, type FormEvent } from "react";
import { EvidenceDialog } from "./EvidenceDialog";
import { UploadCloud, FileText, X, Info, Loader2, ArrowRight } from "lucide-react";
import { api, type Claim } from "./api";

interface EvidenceItem {
  file_id: string;
  original_name: string;
  media_type: string;
  role: string;
  fixture_placeholder?: boolean;
}

interface SupplementaryEvidenceModalProps {
  claim: Claim;
  onClose: () => void;
  onSuccess: () => Promise<void>;
}

export function SupplementaryEvidenceModal({
  claim,
  onClose,
  onSuccess,
}: SupplementaryEvidenceModalProps) {
  const [photos, setPhotos] = useState<File[]>([]);
  const [pages, setPages] = useState<File[]>([]);
  // The claim's current evidence. A new revision lists every file it keeps, so nothing can be
  // committed until both the file list and the current revision have loaded.
  const [existing, setExisting] = useState<
    | { state: "loading" }
    | { state: "failed" }
    | { state: "ready"; files: EvidenceItem[]; fileIds: string[] }
  >({ state: "loading" });
  const [loadAttempt, setLoadAttempt] = useState(0);
  // One key set per selection: a retry of the same upload replays instead of duplicating it.
  const [attemptKey, setAttemptKey] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");

  const nextRevision = (claim.input_revision || 0) + 1;
  const loadingFiles = existing.state === "loading";
  const existingFiles = existing.state === "ready" ? existing.files : [];

  useEffect(() => {
    let active = true;
    setExisting({ state: "loading" });
    Promise.all([
      api<{ files: EvidenceItem[] }>(`/claims/${claim.claim_id}/files`),
      claim.input_revision && claim.input_revision > 0
        ? api<{ file_ids: string[] }>(
            `/claims/${claim.claim_id}/input-revisions/${claim.input_revision}`,
          )
        : Promise.resolve({ file_ids: [] as string[] }),
    ])
      .then(([listed, revision]) => {
        if (!active) return;
        const files = (listed.files || []).filter((f) => !f.fixture_placeholder);
        const real = new Set(files.map((f) => f.file_id));
        setExisting({
          state: "ready",
          files,
          fileIds: (revision.file_ids || []).filter((fid) => real.has(fid)),
        });
      })
      .catch(() => {
        if (active) setExisting({ state: "failed" });
      });
    return () => {
      active = false;
    };
  }, [claim.claim_id, claim.input_revision, loadAttempt]);

  const choose = (set: (files: File[]) => void) => (files: File[]) => {
    set(files);
    setAttemptKey(crypto.randomUUID());
  };

  const existingPhotos = existingFiles.filter(
    (f) => f.role === "photograph" && !f.fixture_placeholder,
  );
  const existingPages = existingFiles.filter(
    (f) => f.role === "estimate_page" && !f.fixture_placeholder,
  );

  const remainingPhotos = Math.max(0, 40 - existingPhotos.length);
  const remainingPages = Math.max(0, 10 - existingPages.length);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!photos.length && !pages.length) {
      setError("Please select at least one supplementary photograph or estimate page to upload.");
      return;
    }
    if (existing.state !== "ready") return;
    const existingFileIds = existing.fileIds;
    setError("");
    setBusy(true);

    try {
      const newUploadedIds: string[] = [];

      // 2. Upload supplementary damage photographs
      if (photos.length > 0) {
        setProgress(`Uploading ${photos.length} photograph(s)...`);
        const photoFormData = new FormData();
        photoFormData.set("role", "photograph");
        for (const file of photos) {
          photoFormData.append("files", file);
        }
        const photoRes = await api<{
          files?: { file_id: string }[];
          file_ids?: string[];
        }>(`/claims/${claim.claim_id}/files`, {
          method: "POST",
          body: photoFormData,
          headers: { "Idempotency-Key": `${attemptKey}.photos` },
        });
        const uploadedIds = photoRes.file_ids ?? photoRes.files?.map((f) => f.file_id) ?? [];
        newUploadedIds.push(...uploadedIds);
      }

      // 3. Upload supplementary estimate pages (if any)
      if (pages.length > 0) {
        setProgress(`Uploading ${pages.length} estimate page(s)...`);
        const pageFormData = new FormData();
        pageFormData.set("role", "estimate_page");
        for (const file of pages) {
          pageFormData.append("files", file);
        }
        const pageRes = await api<{
          files?: { file_id: string }[];
          file_ids?: string[];
        }>(`/claims/${claim.claim_id}/files`, {
          method: "POST",
          body: pageFormData,
          headers: { "Idempotency-Key": `${attemptKey}.pages` },
        });
        const uploadedIds = pageRes.file_ids ?? pageRes.files?.map((f) => f.file_id) ?? [];
        newUploadedIds.push(...uploadedIds);
      }

      // 4. Commit input revision with combined real file IDs
      setProgress(`Starting pipeline evaluation for Input Revision ${nextRevision}...`);
      const allFileIds = Array.from(new Set([...existingFileIds, ...newUploadedIds]));
      await api(`/claims/${claim.claim_id}/input-revisions`, {
        method: "POST",
        body: JSON.stringify({ file_ids: allFileIds }),
        headers: { "Idempotency-Key": `${attemptKey}.commit` },
      });

      // 5. Complete & reload claim review
      await onSuccess();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to upload supplementary evidence.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <EvidenceDialog
      title="Upload Supplementary Evidence"
      subtitle={`Claim ${claim.reference} · Amending to Input Revision ${nextRevision}`}
      onClose={busy ? () => undefined : onClose}
    >
      <form className="supplementary-modal-container" onSubmit={handleSubmit}>
        <div className="supplementary-overview">
          <div className="supplementary-stat">
            <span className="supplementary-stat-label">Current Revision</span>
            <span className="supplementary-stat-value">
              {claim.input_revision ? `Input Rev ${claim.input_revision}` : "None"}
            </span>
          </div>
          <div className="supplementary-stat">
            <span className="supplementary-stat-label">Attached Photos</span>
            <span className="supplementary-stat-value">
              {existing.state === "ready" ? `${existingPhotos.length} / 40` : loadingFiles ? "..." : "—"}
            </span>
          </div>
          <div className="supplementary-stat">
            <span className="supplementary-stat-label">Estimate Pages</span>
            <span className="supplementary-stat-value">
              {existing.state === "ready" ? `${existingPages.length} / 10` : loadingFiles ? "..." : "—"}
            </span>
          </div>
          <div className="supplementary-stat">
            <span className="supplementary-stat-label">Target Revision</span>
            <span className="supplementary-stat-value">
              Input Rev {nextRevision}
            </span>
          </div>
        </div>

        <div className="supplementary-info-card">
          <Info size={18} style={{ flexShrink: 0, marginTop: "2px" }} />
          <div>
            <strong>How amendments work:</strong> Uploading additional damage photographs
            or estimate pages creates a new input revision (FR-06). Unchanged branches
            (such as already parsed estimate line items) are reused, while new evidence is
            automatically processed through the model pipeline to re-evaluate damage, coverage, and
            cost checks.
          </div>
        </div>

        {existing.state === "failed" && (
          <div className="field-error-banner" role="alert">
            The claim&rsquo;s current evidence could not be loaded, so nothing can be added
            yet. Nothing was uploaded.{" "}
            <button
              type="button"
              className="button-link-small"
              onClick={() => setLoadAttempt((n) => n + 1)}
            >
              Try again
            </button>
          </div>
        )}

        {error && (
          <div className="field-error-banner" role="alert">
            {error}
          </div>
        )}

        <div className="upload-grid">
          <ModalFilePicker
            title="Supplementary Photographs"
            detail={`JPEG or PNG · up to ${remainingPhotos} additional`}
            files={photos}
            setFiles={choose(setPhotos)}
            accept="image/jpeg,image/png"
            limit={remainingPhotos}
            disabled={busy || remainingPhotos === 0}
          />
          <ModalFilePicker
            title="Supplementary Estimate Pages"
            detail={`JPEG, PNG or PDF · up to ${remainingPages} additional`}
            files={pages}
            setFiles={choose(setPages)}
            accept="image/jpeg,image/png,application/pdf"
            limit={remainingPages}
            disabled={busy || remainingPages === 0}
          />
        </div>

        <div className="supplementary-actions">
          <button
            type="button"
            className="button button-outline"
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="button button-dark"
            disabled={busy || existing.state !== "ready" || (!photos.length && !pages.length)}
          >
            {busy ? (
              <>
                <Loader2 className="spin" size={16} />
                {progress || "Processing upload..."}
              </>
            ) : (
              <>
                Upload &amp; Reassess (Rev {nextRevision}) <ArrowRight size={16} />
              </>
            )}
          </button>
        </div>
      </form>
    </EvidenceDialog>
  );
}

function ModalFilePicker({
  title,
  detail,
  files,
  setFiles,
  accept,
  limit,
  disabled,
}: {
  title: string;
  detail: string;
  files: File[];
  setFiles: (f: File[]) => void;
  accept: string;
  limit: number;
  disabled: boolean;
}) {
  const [error, setError] = useState("");

  function add(incoming: FileList | null) {
    if (!incoming || disabled) return;
    const list = Array.from(incoming);
    const acceptedTypes = accept.split(",").map((s) => s.trim());
    if (list.some((f) => !acceptedTypes.includes(f.type))) {
      setError("Please choose only supported file types.");
      return;
    }
    if (files.length + list.length > limit) {
      setError(`Cannot add more than ${limit} files.`);
      return;
    }
    if (list.some((f) => f.size > 25 * 1024 * 1024)) {
      setError("Each file must be 25 MB or smaller.");
      return;
    }
    setError("");
    setFiles([...files, ...list]);
  }

  return (
    <div>
      <label
        className={`upload-zone ${disabled ? "upload-zone-disabled" : ""}`}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          if (!disabled) add(e.dataTransfer.files);
        }}
      >
        <span>
          <UploadCloud size={24} />
        </span>
        <strong>{title}</strong>
        <p>
          <b>Choose files</b> or drag them here
        </p>
        <small>{detail}</small>
        <input
          type="file"
          multiple
          accept={accept}
          disabled={disabled}
          onChange={(e) => {
            add(e.target.files);
            e.target.value = "";
          }}
        />
      </label>
      {error && (
        <p className="field-error" role="alert" style={{ marginTop: "6px" }}>
          {error}
        </p>
      )}
      {files.map((f, i) => (
        <div className="upload-file" key={`${f.name}-${i}`}>
          <FileText size={16} />
          <span>
            {f.name}
            <small>{(f.size / 1024 / 1024).toFixed(1)} MB</small>
          </span>
          <button
            type="button"
            className="icon-button"
            aria-label={`Remove ${f.name}`}
            disabled={disabled}
            onClick={() => setFiles(files.filter((_, j) => j !== i))}
          >
            <X size={16} />
          </button>
        </div>
      ))}
    </div>
  );
}

