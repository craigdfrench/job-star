// client/src/api.ts
// Thin client for the Job-Star intake API.
// Uses FormData so future file/voice uploads slot in without changes.

export interface IntakePayload {
  goal: string;
  domain: string;
  urgency: string;
}

export interface IntakeResponse {
  id: string;
  status: string;
  receivedAt: string;
}

const API_BASE =
  (import.meta.env?.VITE_API_BASE as string | undefined) ?? "";

export async function submitIntake(
  payload: IntakePayload
): Promise<IntakeResponse> {
  const form = new FormData();
  form.append("goal", payload.goal);
  form.append("domain", payload.domain);
  form.append("urgency", payload.urgency);

  const res = await fetch(`${API_BASE}/api/intake`, {
    method: "POST",
    body: form,
  });

  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`Intake failed (${res.status}): ${text}`);
  }

  return (await res.json()) as IntakeResponse;
}
