import { DashboardShell } from "../components/dashboard-shell";

const invoices = [
  { label: "Invoice paid", value: "2026-03-12", detail: "USD 240.00" },
  { label: "Renewal date", value: "2026-04-01", detail: "next billing cycle" },
  { label: "Checkout completed", value: "2026-03-01", detail: "seat change applied" }
] as const;

export default function BillingPage() {
  return (
    <DashboardShell
      active="billing"
      title="Billing"
      description="Track the current tier, renewal date, and payment method in one place. This page is intentionally static, but the structure mirrors the future subscription API."
      stats={[
        {
          label: "Plan",
          value: "Team",
          detail: "shared workspace subscription",
          tone: "billing"
        },
        {
          label: "Seats",
          value: "8 / 10",
          detail: "two seats available",
          tone: "usage"
        },
        {
          label: "Payment",
          value: "Visa ending 4581",
          detail: "primary method on file",
          tone: "keys"
        }
      ]}
      actions={[
        { href: "/usage", label: "View usage", variant: "secondary" },
        { href: "/keys", label: "Audit keys" }
      ]}
    >
      <section className="panelList" aria-label="billing overview">
        <article className="panel">
          <h2>Subscription state</h2>
          <div className="banner">
            <p className="bannerTitle">Healthy billing profile</p>
            <p className="bannerCopy">
              The workspace is on a Team plan with enough headroom for the
              current request volume. Renewal and payment details are surfaced
              here so support can resolve issues quickly.
            </p>
          </div>
        </article>

        <article className="panel">
          <h2>Invoice history</h2>
          <ul className="dataList">
            {invoices.map((invoice) => (
              <li key={invoice.label}>
                <span>{invoice.label}</span>
                <span>
                  {invoice.value} - {invoice.detail}
                </span>
              </li>
            ))}
          </ul>
        </article>
      </section>

      <section className="panelList" aria-label="billing details">
        <article className="panel">
          <h2>Included in the plan</h2>
          <ul className="dataList">
            <li>
              <span>Workspace access</span>
              <span>Shared members only</span>
            </li>
            <li>
              <span>Usage controls</span>
              <span>Soft and hard limits</span>
            </li>
            <li>
              <span>Invoice exports</span>
              <span>Monthly PDF, future API-ready</span>
            </li>
          </ul>
        </article>

        <article className="panel">
          <h2>Payment method</h2>
          <p className="panelCopy">
            The control plane should store only the minimum billing status
            required for display. Full payment details stay in the processor.
          </p>
        </article>
      </section>
    </DashboardShell>
  );
}
