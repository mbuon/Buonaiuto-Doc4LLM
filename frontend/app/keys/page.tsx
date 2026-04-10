import { DashboardShell } from "../components/dashboard-shell";

const keys = [
  {
    name: "key_prod_01",
    prefix: "dh_live_7f3a",
    environment: "production",
    lastUsed: "2m ago",
    status: "Active",
    scopes: ["search", "read", "resolve"]
  },
  {
    name: "key_prod_02",
    prefix: "dh_live_92bc",
    environment: "production",
    lastUsed: "17m ago",
    status: "Active",
    scopes: ["search", "read"]
  },
  {
    name: "key_staging",
    prefix: "dh_test_104f",
    environment: "staging",
    lastUsed: "1h ago",
    status: "Rotates weekly",
    scopes: ["search", "query_chunks"]
  }
] as const;

export default function KeysPage() {
  return (
    <DashboardShell
      active="keys"
      title="API Keys"
      description="Keep production and staging credentials visible, scoped, and easy to rotate. The page is static today, but it is shaped for future create/revoke actions."
      stats={[
        {
          label: "Active keys",
          value: "3",
          detail: "2 production, 1 staging",
          tone: "keys"
        },
        {
          label: "Rotation window",
          value: "7 days",
          detail: "recommended cadence",
          tone: "usage"
        },
        {
          label: "Scope model",
          value: "Least privilege",
          detail: "workspace aware by design",
          tone: "billing"
        }
      ]}
      actions={[
        { href: "/usage", label: "Check usage", variant: "secondary" },
        { href: "/billing", label: "Review billing" }
      ]}
    >
      <section className="panelList" aria-label="key inventory">
        <article className="panel">
          <h2>Inventory</h2>
          <div className="stack">
            {keys.map((key) => (
              <article key={key.name} className="keyCard">
                <div className="keyHead">
                  <div>
                    <p className="keyName">{key.name}</p>
                    <p className="keyMeta">{key.prefix}</p>
                  </div>
                  <p className="keyMeta">{key.environment}</p>
                </div>
                <div className="chipRow">
                  {key.scopes.map((scope) => (
                    <span key={scope} className="chip">
                      {scope}
                    </span>
                  ))}
                </div>
                <div className="meterMeta">
                  <p className="metaDetail">Last used {key.lastUsed}</p>
                  <p className="metaDetail">{key.status}</p>
                </div>
              </article>
            ))}
          </div>
        </article>

        <article className="panel">
          <h2>Rotation policy</h2>
          <ul className="dataList">
            <li>
              <span>Production keys</span>
              <span>Rotate manually with overlap</span>
            </li>
            <li>
              <span>Staging keys</span>
              <span>Shorter-lived by default</span>
            </li>
            <li>
              <span>Audit trail</span>
              <span>Created, last used, revoked</span>
            </li>
          </ul>
        </article>
      </section>

      <section className="panelList" aria-label="key operations">
        <article className="panel">
          <h2>Operational guardrails</h2>
          <p className="panelCopy">
            Surface the minimum metadata needed to manage credentials without
            exposing secrets in the dashboard itself.
          </p>
        </article>

        <article className="panel">
          <h2>Future actions</h2>
          <p className="panelCopy">
            Replace the static actions with create, revoke, and rotate handlers
            once the control-plane endpoints are available.
          </p>
        </article>
      </section>
    </DashboardShell>
  );
}
