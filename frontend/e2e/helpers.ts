export const API_BASE_URL =
  process.env.E2E_API_URL ?? "http://localhost:8000";

export const sampleReview = {
  id: "e2e-review-001",
  repositoryName: "octocat/hello-world",
  githubPrNumber: 42,
  status: "completed",
  summary: "Review completed with 1 findings across 3 files.",
  riskScore: 75,
  processingTimeMs: 1200,
  comments: [
    {
      id: "e2e-comment-001",
      title: "Missing auth check",
      severity: "HIGH",
      category: "SECURITY",
    },
  ],
};

export const sampleReviewStats = {
  totalReviews: 1,
  totalComments: 1,
  completedReviews: 1,
  pendingReviews: 0,
  failedReviews: 0,
  averageRiskScore: 75,
  averageProcessingTimeMs: 1200,
};

export const sampleJob = {
  job_id: "e2e-job-001",
  status: "queued",
  repository: "octocat/demo",
  pull_number: 99,
  files_processed: 0,
  chunks_processed: 0,
  comments_generated: 0,
  comments_published: 0,
  error: null,
  created_at: "2026-06-19T12:00:00Z",
  updated_at: "2026-06-19T12:00:00Z",
};

export function buildGraphqlResponse(query: string) {
  if (query.includes("reviewStats")) {
    return { data: { reviewStats: sampleReviewStats } };
  }
  if (query.includes("reviews")) {
    return { data: { reviews: [sampleReview] } };
  }
  return { data: {} };
}
