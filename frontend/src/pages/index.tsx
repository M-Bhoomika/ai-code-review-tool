import Head from "next/head";
import Link from "next/link";
import type { NextPage } from "next";

interface Feature {
  title: string;
  description: string;
}

const features: Feature[] = [
  {
    title: "GitHub App PR Reviews",
    description:
      "Installs as a GitHub App and automatically reviews pull requests, posting actionable feedback directly on the diff.",
  },
  {
    title: "Async Processing",
    description:
      "Webhook events are queued and handled by background workers, keeping reviews fast and resilient under load.",
  },
  {
    title: "LangGraph Workflows",
    description:
      "Review logic is orchestrated as composable LangGraph workflows, making each analysis step explicit and extensible.",
  },
  {
    title: "Analytics Dashboard",
    description:
      "Track review throughput, findings, and model performance with metrics surfaced through a dedicated dashboard.",
  },
];

const Home: NextPage = () => {
  return (
    <>
      <Head>
        <title>AI Code Review Tool</title>
        <meta
          name="description"
          content="AI-powered code review for GitHub pull requests."
        />
      </Head>
      <main className="container">
        <h1 className="title">AI Code Review Tool</h1>
        <p className="subtitle">
          An AI-powered service that reviews GitHub pull requests, processes
          work asynchronously, orchestrates analysis with LangGraph workflows,
          and surfaces insights through an analytics dashboard.
        </p>

        <section className="grid">
          {features.map((feature) => (
            <article key={feature.title} className="card">
              <h2 className="card-title">{feature.title}</h2>
              <p className="card-description">{feature.description}</p>
            </article>
          ))}
        </section>

        <div className="home-actions">
          <Link className="button" href="/dashboard">
            Open Dashboard
          </Link>
        </div>
      </main>
    </>
  );
};

export default Home;
