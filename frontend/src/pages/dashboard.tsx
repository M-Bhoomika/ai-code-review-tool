import Head from "next/head";

import Link from "next/link";

import type { NextPage } from "next";

import { FormEvent, useCallback, useEffect, useState } from "react";



import { triggerReview } from "@/lib/api";

import {

  GraphQLReview,

  GraphQLReviewStats,

  computeSuccessRate,

  fetchReviewStats,

  fetchReviews,

  formatSuccessRate,

} from "@/lib/graphql";



const REFRESH_INTERVAL_MS = 5000;



function statusClass(status: string): string {

  switch (status) {

    case "completed":

      return "badge badge-success";

    case "failed":

      return "badge badge-error";

    case "running":

      return "badge badge-active";

    default:

      return "badge badge-pending";

  }

}



const Dashboard: NextPage = () => {

  const [reviews, setReviews] = useState<GraphQLReview[]>([]);

  const [stats, setStats] = useState<GraphQLReviewStats | null>(null);

  const [loadError, setLoadError] = useState<string | null>(null);



  const [repository, setRepository] = useState("");

  const [pullNumber, setPullNumber] = useState("");

  const [submitting, setSubmitting] = useState(false);

  const [formMessage, setFormMessage] = useState<string | null>(null);

  const [formError, setFormError] = useState<string | null>(null);



  const load = useCallback(async () => {

    try {

      const [reviewList, statSummary] = await Promise.all([

        fetchReviews(),

        fetchReviewStats(),

      ]);

      setReviews(reviewList);

      setStats(statSummary);

      setLoadError(null);

    } catch (err) {

      setLoadError(err instanceof Error ? err.message : "Failed to load data");

    }

  }, []);



  useEffect(() => {

    load();

    const timer = setInterval(load, REFRESH_INTERVAL_MS);

    return () => clearInterval(timer);

  }, [load]);



  const onSubmit = async (event: FormEvent) => {

    event.preventDefault();

    setFormMessage(null);

    setFormError(null);



    const pr = Number(pullNumber);

    if (!repository.includes("/")) {

      setFormError("Repository must be in 'owner/name' format.");

      return;

    }

    if (!Number.isInteger(pr) || pr <= 0) {

      setFormError("Pull request number must be a positive integer.");

      return;

    }



    setSubmitting(true);

    try {

      const job = await triggerReview(repository.trim(), pr);

      setFormMessage(`Review queued for ${job.repository} #${job.pull_number}.`);

      setRepository("");

      setPullNumber("");

      await load();

    } catch (err) {

      setFormError(err instanceof Error ? err.message : "Failed to queue review");

    } finally {

      setSubmitting(false);

    }

  };



  return (

    <>

      <Head>

        <title>Dashboard · AI Code Review Tool</title>

      </Head>

      <main className="container">

        <div className="page-head">

          <div>

            <h1 className="title">Review Dashboard</h1>

            <p className="subtitle">

              Monitor pull request review analytics, throughput, and findings.

            </p>

          </div>

          <Link className="nav-link" href="/">

            ← Home

          </Link>

        </div>



        {loadError && (

          <div className="alert alert-error" role="alert">

            Could not reach the API: {loadError}

          </div>

        )}



        <section className="stats-grid">

          <StatCard label="Total Reviews" value={stats?.totalReviews ?? 0} />

          <StatCard label="Completed" value={stats?.completedReviews ?? 0} />

          <StatCard label="Failed" value={stats?.failedReviews ?? 0} />

          <StatCard label="Pending" value={stats?.pendingReviews ?? 0} />

          <StatCard label="Total Comments" value={stats?.totalComments ?? 0} />

          <StatCard

            label="Success Rate"

            value={

              stats ? formatSuccessRate(computeSuccessRate(stats)) : "0%"

            }

          />

        </section>



        <section className="card form-card">

          <h2 className="card-title">Trigger a Review</h2>

          <form className="trigger-form" onSubmit={onSubmit}>

            <input

              className="input"

              placeholder="owner/repository"

              value={repository}

              onChange={(e) => setRepository(e.target.value)}

              aria-label="Repository"

            />

            <input

              className="input"

              placeholder="PR number"

              value={pullNumber}

              onChange={(e) => setPullNumber(e.target.value)}

              aria-label="Pull request number"

              inputMode="numeric"

            />

            <button className="button" type="submit" disabled={submitting}>

              {submitting ? "Queuing…" : "Start Review"}

            </button>

          </form>

          {formMessage && <p className="form-ok">{formMessage}</p>}

          {formError && <p className="form-err">{formError}</p>}

        </section>



        <section className="card">

          <h2 className="card-title">Recent Reviews</h2>

          {reviews.length === 0 ? (

            <p className="card-description">No review analytics yet.</p>

          ) : (

            <div className="table-wrap">

              <table className="table">

                <thead>

                  <tr>

                    <th>Repository</th>

                    <th>PR</th>

                    <th>Status</th>

                    <th>Comments</th>

                    <th>Risk Score</th>

                  </tr>

                </thead>

                <tbody>

                  {reviews.map((review) => (

                    <tr key={review.id}>

                      <td>{review.repositoryName}</td>

                      <td>{review.githubPrNumber}</td>

                      <td>

                        <span className={statusClass(review.status)}>

                          {review.status}

                        </span>

                      </td>

                      <td>{review.comments.length}</td>

                      <td>{review.riskScore ?? "—"}</td>

                    </tr>

                  ))}

                </tbody>

              </table>

            </div>

          )}

        </section>

      </main>

    </>

  );

};



interface StatCardProps {

  label: string;

  value: number | string;

}



function StatCard({ label, value }: StatCardProps) {

  return (

    <div className="stat-card">

      <div className="stat-value">{value}</div>

      <div className="stat-label">{label}</div>

    </div>

  );

}



export default Dashboard;


