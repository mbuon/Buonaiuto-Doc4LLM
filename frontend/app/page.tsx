import Link from "next/link";
import { DashboardShell } from "./components/dashboard-shell";

const cards = [
  {
    label: "Usage Today",
    value: "1,842",
    detail: "of 10,000 monthly queries",
    tone: "usage"
  },
  {
    label: "Plan",
    value: "Team",
    detail: "Renews on 2026-04-01",
    tone: "billing"
  },
  {
    label: "Active API Keys",
    value: "3",
    detail: "2 production, 1 staging",
    tone: "keys"
  }
] as const;

export default function Page() {
  return (
    <DashboardShell
      active="overview"
      title="Docs Hub Workspace Console"
      description="Monitor quotas, billing, and API keys from one control plane. The dashboard is structured for future API wiring without pretending to be live data."
      stats={[
        {
          label: "Workspace",
          value: "Atlas Labs",
          detail: "production billing account",
          tone: "billing"
        },
        {
          label: "Refresh cadence",
          value: "2 min",
          detail: "summary panels are static",
          tone: "usage"
        },
        {
          label: "Safeguard",
          value: "RLS-ready",
          detail: "future workspace isolation",
          tone: "keys"
        }
      ]}
      actions={[
        {
          href: "/usage",
          label: "Review usage"
        },
        {
          href: "/keys",
          label: "Inspect keys",
          variant: "secondary"
        }
      ]}
    >
      <section className="surfaceGrid" aria-label="control plane sections">
        {cards.map((card) => (
          <article key={card.label} className={`metricCard ${card.tone}`}>
            <p className="metricLabel">{card.label}</p>
            <p className="metricValue">{card.value}</p>
            <p className="metricDetail">{card.detail}</p>
          </article>
        ))}
      </section>

      <section className="routeGrid" aria-label="dashboard sections">
        <article className="panel routePanel">
          <div className="panelHead">
            <div>
              <p className="eyebrow">Usage</p>
              <h2>Requests, tokens, and quota burn</h2>
            </div>
            <Link className="textLink" href="/usage">
              Open usage
            </Link>
          </div>
          <p className="panelCopy">
            Read the workspace&apos;s monthly burn rate, recent traffic, and the
            services driving load before you add more keys or raise limits.
          </p>
        </article>

        <article className="panel routePanel">
          <div className="panelHead">
            <div>
              <p className="eyebrow">Billing</p>
              <h2>Plan, renewal, and invoice history</h2>
            </div>
            <Link className="textLink" href="/billing">
              Open billing
            </Link>
          </div>
          <p className="panelCopy">
            Track the current tier, renewal date, and payment method without
            mixing subscription state into operational views.
          </p>
        </article>

        <article className="panel routePanel">
          <div className="panelHead">
            <div>
              <p className="eyebrow">API Keys</p>
              <h2>Credentials, scopes, and rotation</h2>
            </div>
            <Link className="textLink" href="/keys">
              Open keys
            </Link>
          </div>
          <p className="panelCopy">
            Keep production and staging access visible, label keys by purpose,
            and make rotation a routine operation rather than a fire drill.
          </p>
        </article>
      </section>

      <section className="panelList" aria-label="operational timeline">
        <article className="panel">
          <h2>Operational timeline</h2>
          <ul className="dataList">
            <li>
              <span>Workspace created</span>
              <span>2026-02-15</span>
            </li>
            <li>
              <span>Team plan activated</span>
              <span>2026-03-01</span>
            </li>
            <li>
              <span>Latest invoice paid</span>
              <span>2026-03-12</span>
            </li>
          </ul>
        </article>

        <article className="panel">
          <h2>Key activity</h2>
          <ul className="dataList">
            <li>
              <span>key_prod_01</span>
              <span>Last used 2m ago</span>
            </li>
            <li>
              <span>key_prod_02</span>
              <span>Last used 17m ago</span>
            </li>
            <li>
              <span>key_staging</span>
              <span>Last used 1h ago</span>
            </li>
          </ul>
        </article>
      </section>
    </DashboardShell>
  );
}
