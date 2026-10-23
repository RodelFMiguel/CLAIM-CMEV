import { X, UploadCloud, ShieldCheck, ArrowRight } from "lucide-react";
import type { Claim } from "./api";

interface SupplementaryReopeningModalProps {
  claim: Claim;
  assessmentRevision: number;
  reviewRevision: number;
  inputRevision: number;
  onClose: () => void;
  onProceed: () => void;
}

export function SupplementaryReopeningModal({
  assessmentRevision,
  reviewRevision,
  inputRevision,
  onClose,
  onProceed,
}: SupplementaryReopeningModalProps) {
  const nextInputRevision = inputRevision + 1;

  return (
    <div className="retract-modal-overlay" role="dialog" aria-modal="true">
      <div className="retract-modal-card reopening-modal-card">
        <div className="retract-modal-header">
          <div>
            <div className="modal-eyebrow">POST-DISMANTLING &amp; SUPPLEMENTARY EVIDENCE</div>
            <h3 className="retract-modal-title">File Supplementary Claim</h3>
          </div>
          <button
            type="button"
            className="retract-modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        <div className="retract-modal-body">
          <div className="reopening-audit-banner">
            <div className="reopening-audit-icon">
              <ShieldCheck size={20} color="#059669" />
            </div>
            <div>
              <strong>Finalized Report Remains Frozen &amp; Immutable</strong>
              <p>
                Review Revision {reviewRevision} (Assessment Revision {assessmentRevision}) is finalized and legally frozen. Under insurance survey compliance, finalized decisions cannot be edited or overwritten.
              </p>
            </div>
          </div>

          <div className="reopening-workflow-flow">
            <div className="reopening-step-card frozen">
              <span className="step-tag">CURRENT FROZEN</span>
              <h4>Assessment Rev {assessmentRevision}</h4>
              <p>Locked baseline report &amp; PDF record</p>
            </div>
            <div className="reopening-flow-arrow">
              <ArrowRight size={18} />
            </div>
            <div className="reopening-step-card active">
              <span className="step-tag step-tag-next">NEW REVISION</span>
              <h4>Input Rev {nextInputRevision}</h4>
              <p>Post-dismantling photos &amp; repair pages</p>
            </div>
          </div>

          <div className="reopening-guidance-list">
            <div className="guidance-item">
              <span className="guidance-dot">&bull;</span>
              <div>
                <strong>Hidden Damage After Dismantling:</strong> Upload photographs of concealed damage revealed after bumper, panel, or structural strip-down.
              </div>
            </div>
            <div className="guidance-item">
              <span className="guidance-dot">&bull;</span>
              <div>
                <strong>Supplemental Repair Estimates:</strong> Upload updated workshop estimate sheets for additional repair or replacement parts.
              </div>
            </div>
            <div className="guidance-item">
              <span className="guidance-dot">&bull;</span>
              <div>
                <strong>Automatic Lineage &amp; Carry-Forward:</strong> All prior evidence files will be preserved. The AI pipeline will evaluate the complete revised dossier.
              </div>
            </div>
          </div>

          <div className="retract-modal-actions">
            <button
              type="button"
              className="button button-outline"
              onClick={onClose}
            >
              Cancel
            </button>
            <button
              type="button"
              className="button button-dark button-proceed-reopen"
              onClick={onProceed}
            >
              <UploadCloud size={16} /> Proceed to Upload Evidence
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

