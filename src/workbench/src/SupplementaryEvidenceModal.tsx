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
  const [existingFiles, setExistingFiles] = useState<EvidenceItem[]>([]);
  const [loadingFiles, setLoadingFiles] = useState(true);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");

  const nextRevision = (claim.input_revision || 0) + 1;

  useEffect(() => {
    let active = true;
    api<{ files: EvidenceItem[] }>(`/claims/${claim.claim_id}/files`)
      .then((res) => {
        if (active) {
          setExistingFiles(res.files || []);
          setLoadingFiles(false);
        }
      })
      .catch(() => {
        if (active) setLoadingFiles(false);
      });
    return () => {
      active = false;
    };
  }, [claim.claim_id]);

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
    setError("");
    setBusy(true);

    try {
      // 1. Retrieve file IDs from current input revision if one already exists
      let existingFileIds: string[] = [];
      if (claim.input_revision && claim.input_revision > 0) {
        setProgress("Verifying existing evidence revision...");
        try {
          const revData = await api<{ file_ids?: string[] }>(
            `/claims/${claim.claim_id}/input-revisions/${claim.input_revision}`,
          );
          if (revData?.file_ids) {
            const realFileIds = new Set(
              existingFiles.filter((f) => !f.fixture_placeholder).map((f) => f.file_id),
            );
            existingFileIds = revData.file_ids.filter((fid) => realFileIds.has(fid));
          }
        } catch {
          existingFileIds = [];
        }
      }

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
          headers: { "Idempotency-Key": crypto.randomUUID() },
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
          headers: { "Idempotency-Key": crypto.randomUUID() },
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
        headers: { "Idempotency-Key": crypto.randomUUID() },
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
              {loadingFiles ? "..." : `${existingPhotos.length} / 40`}
            </span>
          </div>
          <div className="supplementary-stat">
            <span className="supplementary-stat-label">Estimate Pages</span>
            <span className="supplementary-stat-value">
              {loadingFiles ? "..." : `${existingPages.length} / 10`}
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
            setFiles={setPhotos}
            accept="image/jpeg,image/png"
            limit={remainingPhotos}
            disabled={busy || remainingPhotos === 0}
          />
          <ModalFilePicker
            title="Supplementary Estimate Pages"
            detail={`JPEG, PNG or PDF · up to ${remainingPages} additional`}
            files={pages}
            setFiles={setPages}
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
            disabled={busy || (!photos.length && !pages.length)}
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

