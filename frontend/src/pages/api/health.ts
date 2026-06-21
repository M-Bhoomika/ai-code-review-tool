import type { NextApiRequest, NextApiResponse } from "next";

interface HealthResponse {
  status: string;
  service: string;
}

export default function handler(
  _req: NextApiRequest,
  res: NextApiResponse<HealthResponse>
): void {
  res.status(200).json({ status: "ok", service: "frontend" });
}
