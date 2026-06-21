import { getApiBaseUrl } from "./api";

export type Severity =
  | "CRITICAL"
  | "HIGH"
  | "MEDIUM"
  | "LOW"
  | "INFO";

export type Category =
  | "BUGS"
  | "SECURITY"
  | "PERFORMANCE"
  | "LOGIC"
  | "MAINTAINABILITY"
  | "CODE_QUALITY"
  | "OTHER";

export interface GraphQLReviewComment {
  id: string;
  title: string;
  severity: Severity;
  category: Category;
}

export interface GraphQLReview {
  id: string;
  repositoryName: string;
  githubPrNumber: number;
  status: string;
  summary: string | null;
  riskScore: number | null;
  processingTimeMs: number | null;
  comments: GraphQLReviewComment[];
}

export interface GraphQLReviewStats {
  totalReviews: number;
  totalComments: number;
  completedReviews: number;
  pendingReviews: number;
  failedReviews: number;
  averageRiskScore: number | null;
  averageProcessingTimeMs: number | null;
}

export interface GraphQLCategoryCount {
  category: Category;
  count: number;
}

interface GraphQLResponse<T> {
  data?: T;
  errors?: Array<{ message: string }>;
}

async function graphqlRequest<T>(
  query: string,
  variables?: Record<string, unknown>
): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}/graphql`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, variables }),
  });
  if (!response.ok) {
    throw new Error(`GraphQL request failed (${response.status})`);
  }

  const body = (await response.json()) as GraphQLResponse<T>;
  if (body.errors?.length) {
    throw new Error(body.errors.map((error) => error.message).join("; "));
  }
  if (!body.data) {
    throw new Error("GraphQL response did not include data");
  }
  return body.data;
}

export async function fetchReviews(): Promise<GraphQLReview[]> {
  const data = await graphqlRequest<{ reviews: GraphQLReview[] }>(`
    query {
      reviews {
        id
        repositoryName
        githubPrNumber
        status
        summary
        riskScore
        processingTimeMs
        comments {
          id
          title
          severity
          category
        }
      }
    }
  `);
  return data.reviews;
}

export async function fetchReviewStats(): Promise<GraphQLReviewStats> {
  const data = await graphqlRequest<{ reviewStats: GraphQLReviewStats }>(`
    query {
      reviewStats {
        totalReviews
        totalComments
        completedReviews
        pendingReviews
        failedReviews
        averageRiskScore
        averageProcessingTimeMs
      }
    }
  `);
  return data.reviewStats;
}

export function computeSuccessRate(stats: GraphQLReviewStats): number {
  const finished = stats.completedReviews + stats.failedReviews;
  return finished ? stats.completedReviews / finished : 0;
}

export function formatSuccessRate(rate: number): string {
  return `${Math.round(rate * 100)}%`;
}
