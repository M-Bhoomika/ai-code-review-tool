export interface ReviewJob {
  job_id: string;
  status: string;
  repository: string;
  pull_number: number | null;
  files_processed: number;
  chunks_processed: number;
  comments_generated: number;
  comments_published: number;
  error: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export function getApiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
}

export async function triggerReview(
  repository: string,
  pullNumber: number,
  installationId = 0
): Promise<ReviewJob> {
  const response = await fetch(`${getApiBaseUrl()}/reviews/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      repository,
      pull_number: pullNumber,
      installation_id: installationId,
    }),
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body?.detail) {
        detail =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail);
      }
    } catch {
      // keep default detail
    }
    throw new Error(detail);
  }
  return (await response.json()) as ReviewJob;
}
