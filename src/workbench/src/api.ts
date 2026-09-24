export interface User {
  id: string;
  name: string;
  email: string;
  role: string;
  initials: string;
}
export interface Claim {
  claim_id: string;
  reference: string;
  policy_number: string;
  surveyor: string;
  vehicle: {
    make: string;
    model: string;
    year: number;
    plate: string;
    vehicle_class: string;
  };
  workshop: string;
  loss_date: string;
  status: string;
  photograph_count: number;
  estimate_row_count: number;
  declared_total: string | null;
  currency: string;
  finding_count: number;
  input_revision: number;
  assessment_revision: number | null;
  review_revision: number;
  source_kind: string;
}
export interface ClaimList {
  items: Claim[];
  total: number;
  stats: {
    open_claims: number;
    open_findings: number;
    photographs_received: number;
  };
}
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public reason?: string,
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    headers,
    credentials: "include",
  });
  if (!response.ok) {
    let message = "Unable to reach the service. Please try again.",
      reason;
    try {
      const body = await response.json();
      const detail = body.detail ?? body;
      message =
        typeof detail === "string" ? detail : (detail.message ?? message);
      reason = detail.reason_code;
    } catch {
      /* no JSON */
    }
    throw new ApiError(response.status, message, reason);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
export const write = <T>(path: string, body: unknown) =>
  api<T>(path, {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "Idempotency-Key": crypto.randomUUID() },
  });
export const money = (
  value: string | number | null | undefined,
  currency = "SGD",
) =>
  value == null
    ? "\u2014"
    : new Intl.NumberFormat("en-SG", {
        style: "currency",
        currency,
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(Number(value));
