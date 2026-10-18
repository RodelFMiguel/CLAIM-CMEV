import { useState } from "react";
import { EvidenceDialog, EvidenceImage } from "./EvidenceDialog";

// Display only: identity, coverage and assignment decisions remain owned by M2/M3.
export interface DamageRow {
  summary_id?: string | null;
  part_code: string;
  side: string;
  identity_status?: string;
  damage_codes?: string[];
  coverage: string;
  reason: string;
  reason_codes?: string[];
  photo_count: number;
  source_kind?: string;
}
export interface DamageObservation {
  observation_id: string;
  photo_id: string;
  damage_code: string;
  damage_confidence: number;
  assignment_status: string;
  part_code?: string | null;
  part_reason?: string | null;
  candidates: { part_code: string; containment: number; rank: number }[];
  background_containment: number;
  bbox_norm?: number[];
}
export interface DamageGroup {
  summary_id: string;
  member_observation_ids: string[];
}
interface Props {
  rows: DamageRow[];
  groups?: DamageGroup[];
  observations?: DamageObservation[];
  files: { file_id: string; original_name: string; url?: string }[];
  onPhoto?: (id: string) => void;
  print?: boolean;
}
interface ViewPhoto {
  id: string;
  url: string;
  name: string;
  observations: DamageObservation[];
}
interface Viewing {
  title: string;
  subtitle: string;
  photos: ViewPhoto[];
}
// "Side: unknown (unconfirmed)" is hidden for now: no served model resolves left or right, so it
// reads the same on almost every card. Set to true to show it again. A known side is always
// shown, and the printed report keeps the label because it is the frozen record.
const SHOW_UNKNOWN_SIDE = false;
const label = (value: string) =>
  value.replaceAll("_", " ").replaceAll("-", " ");
const percent = (value: number) => `${(value * 100).toFixed(1)}%`;
const reasons: Record<string, string> = {
  identity_not_resolved:
    "The part category is recorded, but its physical identity has not been confirmed. Confirm the physical part and its side (or that side does not apply), then confirm whether the photographs cover enough of it.",
  ambiguous_between_parts:
    "The damage region overlaps multiple candidate parts too similarly to assign it confidently.",
  mostly_background:
    "Most of the damage region overlaps an area the part model labelled as background. This does not establish that the damage is absent.",
  below_containment_threshold:
    "Too little of the damage region overlaps the leading candidate part to assign it.",
  no_part_overlap: "The damage region does not overlap an identified part.",
  part_masks_missing:
    "The part mask is unavailable, so candidate overlap could not be measured.",
  part_not_accepted:
    "The leading candidate part did not pass the part model's acceptance checks.",
  awaiting_coverage_confirmation:
    "Confirm whether these photographs show enough of this part to assess damage.",
  processing_failed:
    "Image processing failed; photographic coverage could not be established.",
  no_accepted_part_mask: "No accepted view of this part was found.",
  screen_not_run: "Photograph quality could not be fully assessed.",
  coverage_confirmed_not_enough:
    "The reviewer confirmed that these photographs do not show enough of the part.",
  coverage_confirmation_views_mismatch:
    "The confirmed photographs do not include a view that passes the coverage checks.",
};
const reasonText = (code: string) =>
  reasons[code] ?? `Review reason: ${label(code)}.`;

interface DefectColorDef {
  stroke: string;
  fill: string;
  fillHover: string;
  fillLight: string;
  badgeBg: string;
}

function defectColor(code: string): DefectColorDef {
  switch (code.toLowerCase().replace(/[-_]/g, "")) {
    case "dent":
    case "crease":
      return {
        stroke: "#d97706",
        fill: "rgba(245, 158, 11, 0.22)",
        fillHover: "rgba(245, 158, 11, 0.38)",
        fillLight: "rgba(254, 243, 199, 0.6)",
        badgeBg: "#b45309",
      };
    case "scratch":
      return {
        stroke: "#0284c7",
        fill: "rgba(14, 165, 233, 0.22)",
        fillHover: "rgba(14, 165, 233, 0.38)",
        fillLight: "rgba(224, 242, 254, 0.6)",
        badgeBg: "#0369a1",
      };
    case "crack":
    case "tear":
    case "hole":
      return {
        stroke: "#dc2626",
        fill: "rgba(239, 68, 68, 0.24)",
        fillHover: "rgba(239, 68, 68, 0.40)",
        fillLight: "rgba(254, 226, 226, 0.6)",
        badgeBg: "#b91c1c",
      };
    case "glassshatter":
    case "lampbroken":
      return {
        stroke: "#9333ea",
        fill: "rgba(168, 85, 247, 0.24)",
        fillHover: "rgba(168, 85, 247, 0.40)",
        fillLight: "rgba(243, 232, 255, 0.6)",
        badgeBg: "#7e22ce",
      };
    case "tireflat":
      return {
        stroke: "#059669",
        fill: "rgba(16, 185, 129, 0.22)",
        fillHover: "rgba(16, 185, 129, 0.38)",
        fillLight: "rgba(209, 250, 229, 0.6)",
        badgeBg: "#047857",
      };
    default:
      return {
        stroke: "#ea580c",
        fill: "rgba(234, 88, 12, 0.22)",
        fillHover: "rgba(234, 88, 12, 0.38)",
        fillLight: "rgba(255, 237, 213, 0.6)",
        badgeBg: "#c2410c",
      };
  }
}

export function DamageSummary({
  rows,
  groups = [],
  observations = [],
  files,
  onPhoto,
  print = false,
}: Props) {
  const observationsById = new Map(
    observations.map((o) => [o.observation_id, o]),
  );
  const groupsById = new Map(groups.map((g) => [g.summary_id, g]));
  const names = new Map(files.map((f) => [f.file_id, f.original_name]));
  const urls = new Map(
    files.flatMap((f) => (f.url ? [[f.file_id, f.url]] : [])),
  );
  const [viewing, setViewing] = useState<Viewing | null>(null);
  const unresolved = (row: DamageRow) =>
    row.identity_status === "unresolved" || row.part_code === "unresolved";

  function card(row: DamageRow, index: number) {
    // Join only by recorded membership, never by guessed part/side or array position.
    const group = row.summary_id ? groupsById.get(row.summary_id) : undefined;
    const members = (group?.member_observation_ids ?? []).flatMap((id) => {
      const observation = observationsById.get(id);
      return observation ? [observation] : [];
    });
    const codes = row.damage_codes;
    const coverageLabel =
      row.coverage === "unresolved"
        ? row.reason_codes?.includes("identity_not_resolved")
          ? "Needs confirmation"
          : "Not established"
        : row.coverage === "inadequate"
          ? "Not adequate"
          : label(row.coverage);
    const source =
      row.source_kind === "real"
        ? "Based on model analysis of uploaded photographs."
        : row.source_kind === "fixture"
          ? "Illustrative fixture; no model analysed the uploaded photographs."
          : "Observation source not recorded.";
    // Only model-analysed rows link to uploaded pixels; fixture photograph IDs are demonstration IDs.
    const photos: ViewPhoto[] =
      print || row.source_kind !== "real"
        ? []
        : [...new Set(members.map((o) => o.photo_id))].flatMap((id) => {
            const url = urls.get(id);
            return url
              ? [
                  {
                    id,
                    url,
                    name: names.get(id) ?? id,
                    observations: members.filter((o) => o.photo_id === id),
                  },
                ]
              : [];
          });
    const heading = unresolved(row) ? "Part uncertain" : label(row.part_code);
    const sideText =
      row.side === "unknown"
        ? "Side: unknown (unconfirmed)"
        : `Side: ${label(row.side)}`;
    const showSide = row.side !== "unknown" || SHOW_UNKNOWN_SIDE || print;
    const detail = members.map((o) => (
      <li key={o.observation_id}>
        <strong>{label(o.damage_code)}</strong> · Model confidence:{" "}
        {percent(o.damage_confidence)}
        <p>
          {onPhoto && names.has(o.photo_id) && row.source_kind === "real" ? (
            <button
              type="button"
              className="damage-photo-link"
              onClick={() => onPhoto(o.photo_id)}
            >
              View {names.get(o.photo_id)}
            </button>
          ) : (
            (names.get(o.photo_id) ?? `Photograph ${o.photo_id}`)
          )}
        </p>
        {o.assignment_status === "unresolved" &&
          o.part_reason &&
          !row.reason_codes?.includes(o.part_reason) && (
            <p>{reasonText(o.part_reason)}</p>
          )}
        {o.part_reason === "part_masks_missing" ? (
          <p>Candidate overlap unavailable.</p>
        ) : (
          <>
            <p>
              {o.candidates.length
                ? "Candidate parts — share of damage region:"
                : "No candidate part overlaps this region."}
            </p>
            {o.candidates.length > 0 && (
              <ul>
                {o.candidates.map((c) => (
                  <li key={c.rank}>
                    {label(c.part_code)}: {percent(c.containment)} overlap
                  </li>
                ))}
              </ul>
            )}
            <p>
              Part-model background: {percent(o.background_containment)}{" "}
              overlap.
            </p>
          </>
        )}
      </li>
    ));
    const details = (
      <>
        <p className="damage-detail-note">
          Candidate percentages measure mask overlap, not assignment
          probability.
        </p>
        <ul className="damage-observations">{detail}</ul>
      </>
    );
    return (
      <article className="damage-item" key={row.summary_id ?? index}>
        <div className="damage-title">
          <strong>{heading}</strong>
          {showSide && <span className="muted">{sideText}</span>}
          {photos.length > 0 && (
            <button
              type="button"
              className="button button-outline button-small damage-evidence-button"
              aria-haspopup="dialog"
              aria-label={`View evidence for ${heading}${codes?.length ? `, ${codes.map(label).join(", ")}` : ""}`}
              onClick={() =>
                setViewing({
                  title: `${heading.replace(/\b\w/g, (c) => c.toUpperCase())}: photographic evidence`,
                  subtitle: [
                    codes?.length
                      ? `Predicted damage: ${codes.map(label).join(", ")}`
                      : "",
                    showSide ? sideText : "",
                  ]
                    .filter(Boolean)
                    .join(" · "),
                  photos,
                })
              }
            >
              View evidence
            </button>
          )}
        </div>
        <p className="damage-types">
          <strong>
            {row.source_kind === "fixture"
              ? "Illustrative damage"
              : "Predicted damage"}
            :{" "}
          </strong>
          {codes === undefined
            ? "Details unavailable"
            : codes.length
              ? codes.map(label).join(", ")
              : "No damage observation recorded"}
        </p>
        <span className="result-info">Coverage: {coverageLabel}</span>
        {row.reason_codes?.length ? (
          [...new Set(row.reason_codes)].map((code) => (
            <p key={code}>{reasonText(code)}</p>
          ))
        ) : row.reason_codes ? null : (
          <p>{row.reason}</p>
        )}
        <small>
          {source} {row.photo_count}{" "}
          {row.photo_count === 1 ? "photograph" : "photographs"}.
        </small>
        {members.length > 0 ? (
          print || unresolved(row) ? (
            details
          ) : (
            <details className="damage-details">
              <summary>Source observations ({members.length})</summary>
              {details}
            </details>
          )
        ) : codes?.length ? (
          <p>Source observation details unavailable for this assessment.</p>
        ) : null}
      </article>
    );
  }
  const assignedRows = rows.filter((row) => !unresolved(row));
  const uncertainRows = rows.filter(unresolved);
  return (
    <>
      <div className="damage-summary-list">
        {assignedRows.map(card)}
        {uncertainRows.length > 0 &&
          (print ? (
            <section>
              <h3>Unresolved observations ({uncertainRows.length})</h3>
              {uncertainRows.map(card)}
            </section>
          ) : (
            <details className="damage-unresolved" open>
              <summary>
                Unresolved observations ({uncertainRows.length})
              </summary>
              <p>
                Damage was predicted, but its part could not be assigned. These
                observations do not establish an unsupported repair.
              </p>
              {uncertainRows.map(card)}
            </details>
          ))}
      </div>
      {viewing && (
        <DamageEvidenceView
          viewing={viewing}
          onClose={() => setViewing(null)}
        />
      )}
    </>
  );
}

function DamagePhotoView({
  photo,
  showOverlay,
  activeFilter,
  hoveredId,
  onHover,
}: {
  photo: ViewPhoto;
  showOverlay: boolean;
  activeFilter: string | null;
  hoveredId: string | null;
  onHover: (id: string | null) => void;
}) {
  const [failed, setFailed] = useState(false);

  if (failed) {
    return (
      <p className="evidence-dialog-note">
        This image could not be loaded. Close this view, then refresh or use
        &ldquo;Open original file&rdquo; in the Evidence panel.
      </p>
    );
  }

  const visibleObservations = photo.observations.filter(
    (o) => !activeFilter || o.damage_code === activeFilter,
  );

  return (
    <div className="damage-photo-wrapper">
      <img
        className="evidence-dialog-image"
        src={photo.url}
        alt={`Photograph ${photo.name}`}
        onError={() => setFailed(true)}
      />
      {showOverlay && visibleObservations.length > 0 && (
        <div className="damage-overlay-container">
          {visibleObservations.map((obs) => {
            if (!obs.bbox_norm || obs.bbox_norm.length < 4) return null;
            const [x0, y0, x1, y1] = obs.bbox_norm;
            const color = defectColor(obs.damage_code);
            const isHovered = hoveredId === obs.observation_id;
            const badgeInside = y0 < 0.06;
            const badgeAtRight = x0 > 0.75;

            return (
              <div
                key={obs.observation_id}
                className={`damage-defect-box ${isHovered ? "hovered" : ""}`}
                style={{
                  left: `${x0 * 100}%`,
                  top: `${y0 * 100}%`,
                  width: `${Math.max(0.6, (x1 - x0) * 100)}%`,
                  height: `${Math.max(0.6, (y1 - y0) * 100)}%`,
                  borderColor: color.stroke,
                  backgroundColor: isHovered ? color.fillHover : color.fill,
                  boxShadow: isHovered
                    ? `0 0 0 3px ${color.stroke}, 0 4px 14px rgba(0,0,0,0.4)`
                    : `0 0 0 1px rgba(0,0,0,0.3), inset 0 0 0 1px rgba(255,255,255,0.2)`,
                  zIndex: isHovered ? 15 : 5,
                }}
                onMouseEnter={() => onHover(obs.observation_id)}
                onMouseLeave={() => onHover(null)}
              >
                <span
                  className="damage-defect-badge"
                  style={{
                    backgroundColor: color.badgeBg,
                    color: "#ffffff",
                    top: badgeInside ? "3px" : "-24px",
                    left: badgeAtRight ? "auto" : "0",
                    right: badgeAtRight ? "0" : "auto",
                  }}
                >
                  {label(obs.damage_code)} · {percent(obs.damage_confidence)}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function DamageEvidenceView({
  viewing,
  onClose,
}: {
  viewing: Viewing;
  onClose: () => void;
}) {
  const [showOverlay, setShowOverlay] = useState(true);
  const [activeFilter, setActiveFilter] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  const allObservations = viewing.photos.flatMap((p) => p.observations);
  const uniqueCodes = Array.from(
    new Set(allObservations.map((o) => o.damage_code)),
  );

  return (
    <EvidenceDialog
      title={viewing.title}
      subtitle={viewing.subtitle}
      onClose={onClose}
    >
      <div className="damage-dialog-toolbar">
        <div className="damage-dialog-filters">
          <span className="damage-dialog-filter-label">Defect filter:</span>
          <button
            type="button"
            className={`damage-filter-pill ${activeFilter === null ? "active" : ""}`}
            onClick={() => setActiveFilter(null)}
          >
            All ({allObservations.length})
          </button>
          {uniqueCodes.map((code) => {
            const count = allObservations.filter(
              (o) => o.damage_code === code,
            ).length;
            const color = defectColor(code);
            const isActive = activeFilter === code;
            return (
              <button
                key={code}
                type="button"
                className={`damage-filter-pill ${isActive ? "active" : ""}`}
                style={
                  isActive
                    ? {
                        borderColor: color.stroke,
                        backgroundColor: color.badgeBg,
                        color: "#ffffff",
                      }
                    : {}
                }
                onClick={() => setActiveFilter(isActive ? null : code)}
              >
                <span
                  className="damage-pill-dot"
                  style={{
                    backgroundColor: isActive ? "#ffffff" : color.stroke,
                  }}
                />
                {label(code)} ({count})
              </button>
            );
          })}
        </div>
        <label className="damage-dialog-toggle">
          <input
            type="checkbox"
            checked={showOverlay}
            onChange={(e) => setShowOverlay(e.target.checked)}
          />
          <span>Show defect overlay</span>
        </label>
      </div>

      {viewing.photos.map((photo) => (
        <figure className="evidence-dialog-figure" key={photo.id}>
          <DamagePhotoView
            photo={photo}
            showOverlay={showOverlay}
            activeFilter={activeFilter}
            hoveredId={hoveredId}
            onHover={setHoveredId}
          />
          <figcaption className="damage-photo-figcaption">
            <strong>{photo.name}</strong>
            {photo.observations.length > 0 ? (
              <div className="damage-photo-obs-list">
                {photo.observations.map((o) => {
                  const color = defectColor(o.damage_code);
                  const isFiltered =
                    activeFilter && o.damage_code !== activeFilter;
                  const isHovered = hoveredId === o.observation_id;
                  return (
                    <button
                      key={o.observation_id}
                      type="button"
                      className={`damage-obs-chip ${isHovered ? "hovered" : ""} ${isFiltered ? "dimmed" : ""}`}
                      style={{
                        borderColor: color.stroke,
                        backgroundColor: isHovered
                          ? color.fillHover
                          : isFiltered
                            ? "#f1f5f9"
                            : color.fillLight,
                        opacity: isFiltered ? 0.45 : 1,
                      }}
                      onMouseEnter={() => setHoveredId(o.observation_id)}
                      onMouseLeave={() => setHoveredId(null)}
                      onClick={() =>
                        setActiveFilter(
                          activeFilter === o.damage_code ? null : o.damage_code,
                        )
                      }
                      title={`Click to filter to ${label(o.damage_code)}`}
                    >
                      <span
                        className="damage-chip-indicator"
                        style={{ backgroundColor: color.stroke }}
                      />
                      <strong>{label(o.damage_code)}</strong>
                      <span className="damage-chip-conf">
                        {percent(o.damage_confidence)}
                      </span>
                      {o.part_code && (
                        <span className="damage-chip-part">
                          ({label(o.part_code)})
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            ) : (
              <p className="muted">
                No damage observations on this photograph.
              </p>
            )}
          </figcaption>
        </figure>
      ))}

      <p className="evidence-dialog-note">
        {showOverlay
          ? "Defect regions are predicted by the damage segmentation model (CarDD) and mapped to photograph coordinates. Hover or click a defect tag above to isolate and inspect that defect type."
          : "Photographs are shown in original unprocessed form. Check 'Show defect overlay' to view predicted defect regions."}
      </p>
    </EvidenceDialog>
  );
}
