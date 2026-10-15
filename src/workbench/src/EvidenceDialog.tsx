import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";

// Enlarged evidence views. Display only: nothing here changes a finding, a link or a decision.

interface DialogProps {
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
}

/**
 * A modal popup built on the native <dialog> element: focus stays inside it, Esc closes it and the
 * page behind is inert. Render it only while it should be open; closing is the parent's decision.
 */
export function EvidenceDialog({
  title,
  subtitle,
  onClose,
  children,
}: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  // Where focus returns on close. Captured while rendering, before the dialog takes focus.
  const opener = useRef<HTMLElement | null>(
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null,
  );
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    // Esc closes a modal dialog and fires "close". React StrictMode re-runs this effect on the same
    // element, so a close event queued by the first run must not close the second.
    const onNativeClose = () => {
      if (!dialog.open) closeRef.current();
    };
    dialog.addEventListener("close", onNativeClose);
    if (!dialog.open) dialog.showModal();
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      dialog.removeEventListener("close", onNativeClose);
      document.body.style.overflow = overflow;
      opener.current?.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className="evidence-dialog"
      aria-labelledby={titleId}
      onClick={(event) => {
        // The backdrop belongs to the dialog element; everything inside it is a child.
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <header className="evidence-dialog-head">
        <div>
          <h2 id={titleId}>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        <button
          type="button"
          className="button button-outline evidence-dialog-close"
          aria-label="Close evidence view"
          onClick={onClose}
        >
          <X size={20} aria-hidden="true" />
        </button>
      </header>
      <div className="evidence-dialog-body">{children}</div>
    </dialog>
  );
}

export function EvidenceImage({ url, alt }: { url: string; alt: string }) {
  const [failed, setFailed] = useState(false);
  if (failed)
    return (
      <p className="evidence-dialog-note">
        This image could not be loaded. Close this view, then refresh or use
        &ldquo;Open original file&rdquo; in the Evidence panel.
      </p>
    );
  return (
    <img
      className="evidence-dialog-image"
      src={url}
      alt={alt}
      onError={() => setFailed(true)}
    />
  );
}

export function EvidenceFile({
  file,
}: {
  file: { url: string; original_name: string; media_type: string };
}) {
  return file.media_type === "application/pdf" ? (
    <iframe
      className="evidence-dialog-frame"
      title={`Original estimate: ${file.original_name}`}
      src={file.url}
    />
  ) : (
    <EvidenceImage
      url={file.url}
      alt={`Original evidence: ${file.original_name}`}
    />
  );
}

interface PageRef {
  page_id: string;
  page_number: number;
  corrected_url?: string | null;
}
interface RowBox {
  entry_id: string;
  page_id: string;
  row_box_norm: number[];
}
interface MarkBox {
  mark_id: string;
  page_id: string;
  box_norm: number[];
  state: string;
}

/**
 * The corrected estimate page with the extracted rows (blue) and pen marks (red; grey if rejected)
 * drawn over it in page-relative coordinates. With `centerOnSelected` the enclosing popup scrolls so
 * the selected row is in the middle.
 */
export function CorrectedPageView({
  page,
  rows,
  marks,
  selectedRow = null,
  centerOnSelected = false,
}: {
  page: PageRef;
  rows: RowBox[];
  marks: MarkBox[];
  selectedRow?: string | null;
  centerOnSelected?: boolean;
}) {
  const marker = useRef<HTMLSpanElement>(null);
  const center = () => {
    const target = marker.current;
    const scroller = target?.closest<HTMLElement>(".evidence-dialog-body");
    if (!target || !scroller) return;
    scroller.scrollTop +=
      target.getBoundingClientRect().top -
      scroller.getBoundingClientRect().top -
      scroller.clientHeight / 2;
  };
  // The image has no height until it loads, so the same function also runs from onLoad.
  useEffect(center, [selectedRow, page.page_id, centerOnSelected]);
  if (!page.corrected_url) return null;
  const selected = rows.find(
    (r) => r.entry_id === selectedRow && r.page_id === page.page_id,
  );
  return (
    <div style={{ position: "relative" }}>
      <img
        src={page.corrected_url}
        alt={`Corrected estimate page ${page.page_number}`}
        style={{ display: "block", width: "100%" }}
        onLoad={center}
      />
      <svg
        viewBox="0 0 1 1"
        preserveAspectRatio="none"
        aria-label="Extracted rows and pen marks"
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          pointerEvents: "none",
        }}
      >
        {rows
          .filter((r) => r.page_id === page.page_id)
          .map((r) => {
            const [x0, y0, x1, y1] = r.row_box_norm;
            return (
              <rect
                key={r.entry_id}
                x={x0}
                y={y0}
                width={x1 - x0}
                height={y1 - y0}
                fill={r.entry_id === selectedRow ? "#2563eb22" : "none"}
                stroke="#2563eb"
                strokeWidth="2"
                vectorEffect="non-scaling-stroke"
              />
            );
          })}
        {marks
          .filter((m) => m.page_id === page.page_id)
          .map((m) => {
            const [x0, y0, x1, y1] = m.box_norm;
            return (
              <rect
                key={m.mark_id}
                x={x0}
                y={y0}
                width={x1 - x0}
                height={y1 - y0}
                fill="none"
                stroke={m.state === "rejected" ? "#777" : "#dc2626"}
                strokeWidth="2"
                vectorEffect="non-scaling-stroke"
              />
            );
          })}
      </svg>
      {centerOnSelected && selected && (
        <span
          ref={marker}
          aria-hidden="true"
          style={{
            position: "absolute",
            left: 0,
            top: `${selected.row_box_norm[1] * 100}%`,
            width: 1,
            height: 1,
            pointerEvents: "none",
          }}
        />
      )}
    </div>
  );
}
