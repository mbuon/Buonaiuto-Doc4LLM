import { DashboardShell } from "../components/dashboard-shell";

const overallStats = {
  total: 247,
  satisfied: 198,
  unsatisfied: 49,
  satisfactionRate: 0.802,
} as const;

const byTechnology = [
  { technology: "react", total: 84, satisfied: 72, rate: 0.857 },
  { technology: "nextjs", total: 62, satisfied: 51, rate: 0.823 },
  { technology: "python", total: 45, satisfied: 34, rate: 0.756 },
  { technology: "typescript", total: 31, satisfied: 24, rate: 0.774 },
  { technology: "tailwind", total: 25, satisfied: 17, rate: 0.680 },
] as const;

const byDocument = [
  { technology: "react", relPath: "docs/hooks.md", total: 38, satisfied: 34, rate: 0.895 },
  { technology: "nextjs", relPath: "docs/app-router.md", total: 29, satisfied: 22, rate: 0.759 },
  { technology: "python", relPath: "docs/asyncio.md", total: 18, satisfied: 11, rate: 0.611 },
  { technology: "react", relPath: "docs/server-components.md", total: 15, satisfied: 14, rate: 0.933 },
  { technology: "tailwind", relPath: "docs/responsive.md", total: 12, satisfied: 7, rate: 0.583 },
] as const;

const recentFeedback = [
  { id: 247, technology: "react", relPath: "docs/hooks.md", query: "useEffect cleanup pattern", satisfied: true, reason: "Clear example with dependency array explanation", requester: "claude-agent-12", createdAt: "2026-04-09T14:22:00Z" },
  { id: 246, technology: "nextjs", relPath: "docs/app-router.md", query: "nested layouts", satisfied: false, reason: "Missing parallel routes example for my use case", requester: "copilot-session-8", createdAt: "2026-04-09T13:45:00Z" },
  { id: 245, technology: "python", relPath: "docs/asyncio.md", query: "task cancellation", satisfied: false, reason: "Documentation covers basic usage but not cancellation scopes", requester: "claude-agent-7", createdAt: "2026-04-09T12:30:00Z" },
  { id: 244, technology: "react", relPath: "docs/server-components.md", query: "data fetching pattern", satisfied: true, reason: "Exactly the server component data flow I needed", requester: "gemini-agent-3", createdAt: "2026-04-09T11:15:00Z" },
  { id: 243, technology: "tailwind", relPath: "docs/responsive.md", query: "container queries", satisfied: false, reason: "No container query examples, only media queries", requester: "claude-agent-12", createdAt: "2026-04-09T10:00:00Z" },
] as const;

function pct(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

export default function FeedbackPage() {
  return (
    <DashboardShell
      active="feedback"
      title="Documentation Feedback"
      description="Quality signals from LLM agents and requesters. Every documentation retrieval requires mandatory feedback — this page surfaces satisfaction rates, gaps, and per-document breakdowns."
      stats={[
        {
          label: "Total feedback",
          value: String(overallStats.total),
          detail: "all-time submissions",
          tone: "feedback",
        },
        {
          label: "Satisfaction rate",
          value: pct(overallStats.satisfactionRate),
          detail: `${overallStats.satisfied} satisfied / ${overallStats.unsatisfied} unsatisfied`,
          tone: "feedback",
        },
        {
          label: "Coverage gap",
          value: String(overallStats.unsatisfied),
          detail: "docs that missed the mark",
          tone: "neutral",
        },
      ]}
      actions={[
        { href: "/usage", label: "View usage", variant: "secondary" },
      ]}
    >
      {/* Overall satisfaction meter */}
      <section className="panelList" aria-label="satisfaction overview">
        <article className="panel">
          <h2>Overall satisfaction</h2>
          <div className="meterGrid">
            <div className="meter">
              <div className="meterMeta">
                <div>
                  <p className="metricLabel">Satisfaction rate</p>
                  <p className="metaValue">{overallStats.satisfied} / {overallStats.total}</p>
                </div>
                <p className="metaDetail">{pct(overallStats.satisfactionRate)} of requesters found what they needed</p>
              </div>
              <div className="meterBar" aria-hidden="true">
                <div
                  className="meterFill feedback"
                  style={{ width: pct(overallStats.satisfactionRate) }}
                />
              </div>
            </div>
          </div>
        </article>
      </section>

      {/* Per-technology breakdown */}
      <section className="panelList" aria-label="technology breakdown">
        <article className="panel">
          <h2>By technology</h2>
          <ul className="dataList">
            {byTechnology.map((tech) => (
              <li key={tech.technology}>
                <span>{tech.technology}</span>
                <span>{pct(tech.rate)} satisfied ({tech.satisfied}/{tech.total})</span>
              </li>
            ))}
          </ul>
        </article>

        <article className="panel">
          <h2>By document</h2>
          <p className="panelCopy" style={{ marginBottom: "0.75rem" }}>
            Top documents by feedback volume. Low satisfaction rates indicate content gaps to address.
          </p>
          <ul className="dataList">
            {byDocument.map((doc) => (
              <li key={`${doc.technology}/${doc.relPath}`}>
                <span>{doc.technology}/{doc.relPath}</span>
                <span style={{ color: doc.rate < 0.7 ? "var(--accent-billing)" : "inherit" }}>
                  {pct(doc.rate)} ({doc.satisfied}/{doc.total})
                </span>
              </li>
            ))}
          </ul>
        </article>
      </section>

      {/* Recent feedback entries */}
      <section className="panelList" aria-label="recent feedback">
        <article className="panel">
          <h2>Recent feedback</h2>
          <p className="panelCopy" style={{ marginBottom: "0.75rem" }}>
            Latest quality reports from LLM agents. Each entry is a mandatory response after documentation retrieval.
          </p>
          <ul className="dataList">
            {recentFeedback.map((entry) => (
              <li key={entry.id} style={{ flexDirection: "column", alignItems: "flex-start", gap: "0.25rem" }}>
                <span style={{ display: "flex", gap: "0.5rem", alignItems: "center", width: "100%" }}>
                  <span style={{
                    display: "inline-block",
                    width: "0.6rem",
                    height: "0.6rem",
                    borderRadius: "50%",
                    background: entry.satisfied ? "var(--accent-usage)" : "var(--accent-billing)",
                    flexShrink: 0,
                  }} aria-label={entry.satisfied ? "satisfied" : "unsatisfied"} />
                  <span style={{ fontWeight: 500 }}>{entry.technology}/{entry.relPath}</span>
                  <span style={{ marginLeft: "auto", color: "var(--muted)", fontSize: "0.85rem" }}>
                    {entry.requester}
                  </span>
                </span>
                <span style={{ color: "var(--muted)", fontSize: "0.9rem", paddingLeft: "1.1rem" }}>
                  Q: {entry.query}
                </span>
                <span style={{ fontSize: "0.9rem", paddingLeft: "1.1rem" }}>
                  {entry.reason}
                </span>
              </li>
            ))}
          </ul>
        </article>
      </section>

      {/* Explanation panel */}
      <section className="panelList" aria-label="feedback notes">
        <article className="panel">
          <h2>How feedback works</h2>
          <p className="panelCopy">
            Every time an LLM agent retrieves documentation via <code>read_doc</code>,{" "}
            <code>search_docs</code>, or <code>search_documentation</code>, it is required
            to call <code>submit_feedback</code> with a yes/no satisfaction rating and a reason.
            This data drives documentation quality improvements and identifies content gaps.
          </p>
        </article>

        <article className="panel">
          <h2>What belongs here</h2>
          <p className="panelCopy">
            This page is ready to wire into the control-plane API. The static data above
            shows the layout for satisfaction trends, per-technology and per-document breakdowns,
            and a feed of recent feedback entries with requester attribution.
          </p>
        </article>
      </section>
    </DashboardShell>
  );
}
