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
  part_reason?: string | null;
  candidates: { part_code: string; containment: number; rank: number }[];
  background_containment: number;
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
          <span className="muted">
            {row.side === "unknown"
              ? "Side: unknown (unconfirmed)"
              : `Side: ${label(row.side)}`}
          </span>
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
                    row.side === "unknown"
                      ? "Side: unknown (unconfirmed)"
                      : `Side: ${label(row.side)}`,
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
        <EvidenceDialog
          title={viewing.title}
          subtitle={viewing.subtitle}
          onClose={() => setViewing(null)}
        >
          {viewing.photos.map((photo) => (
            <figure className="evidence-dialog-figure" key={photo.id}>
              <EvidenceImage url={photo.url} alt={`Photograph ${photo.name}`} />
              <figcaption>
                <strong>{photo.name}</strong>
                {": "}
                {photo.observations
                  .map(
                    (o) =>
                      `${label(o.damage_code)}, model confidence ${percent(o.damage_confidence)}`,
                  )
                  .join("; ")}
              </figcaption>
            </figure>
          ))}
          <p className="evidence-dialog-note">
            Photographs are shown as uploaded. The predicted damage region is
            not outlined here.
          </p>
        </EvidenceDialog>
      )}
    </>
  );
}
