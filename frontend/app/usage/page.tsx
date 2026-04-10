import { DashboardShell } from "../components/dashboard-shell";

const meters = [
  {
    label: "Monthly requests",
    value: "18,420 / 25,000",
    detail: "74% of quota consumed",
    tone: "usage",
    width: "74%"
  },
  {
    label: "Embedding tokens",
    value: "8.4M / 12M",
    detail: "primarily ingestion workloads",
    tone: "billing",
    width: "70%"
  },
  {
    label: "Cached answers",
    value: "1.2M served",
    detail: "reduced repeat retrieval load",
    tone: "keys",
    width: "58%"
  }
] as const;

const traffic = [
  { label: "search_documentation", value: "9,820 calls", detail: "44% of total traffic" },
  { label: "resolve_library_id", value: "4,240 calls", detail: "mostly cold starts" },
  { label: "read_full_page", value: "3,101 calls", detail: "high-citation workloads" },
  { label: "query_chunks", value: "1,259 calls", detail: "debug and fallback usage" }
] as const;

export default function UsagePage() {
  return (
    <DashboardShell
      active="usage"
      title="Usage"
      description="See quota burn, request shape, and the load profile behind the workspace. The numbers are static, but the layout is ready for metering data from the control plane."
      stats={[
        {
          label: "Current month",
          value: "74%",
          detail: "quota consumed",
          tone: "usage"
        },
        {
          label: "Daily average",
          value: "612",
          detail: "requests per day",
          tone: "neutral"
        },
        {
          label: "Peak window",
          value: "09:00-11:00",
          detail: "highest query volume",
          tone: "keys"
        }
      ]}
      actions={[
        { href: "/billing", label: "Check plan", variant: "secondary" },
        { href: "/keys", label: "Review keys" }
      ]}
    >
      <section className="panelList" aria-label="usage meters">
        <article className="panel">
          <h2>Quota burn</h2>
          <div className="meterGrid">
            {meters.map((meter) => (
              <div key={meter.label} className="meter">
                <div className="meterMeta">
                  <div>
                    <p className="metricLabel">{meter.label}</p>
                    <p className="metaValue">{meter.value}</p>
                  </div>
                  <p className="metaDetail">{meter.detail}</p>
                </div>
                <div className="meterBar" aria-hidden="true">
                  <div
                    className={`meterFill ${meter.tone}`}
                    style={{ width: meter.width }}
                  />
                </div>
              </div>
            ))}
          </div>
        </article>

        <article className="panel">
          <h2>Traffic mix</h2>
          <ul className="dataList">
            {traffic.map((item) => (
              <li key={item.label}>
                <span>{item.label}</span>
                <span>
                  {item.value} - {item.detail}
                </span>
              </li>
            ))}
          </ul>
        </article>
      </section>

      <section className="panelList" aria-label="usage notes">
        <article className="panel">
          <h2>What belongs here</h2>
          <p className="panelCopy">
            This route is the place for per-workspace metering, quota alerts,
            and exportable usage history once the control-plane API is wired in.
          </p>
        </article>

        <article className="panel">
          <h2>Operational signals</h2>
          <ul className="dataList">
            <li>
              <span>Usage alerts</span>
              <span>Enabled at 80%</span>
            </li>
            <li>
              <span>Soft limit</span>
              <span>25,000 requests</span>
            </li>
            <li>
              <span>Hard stop</span>
              <span>30,000 requests</span>
            </li>
          </ul>
        </article>
      </section>
    </DashboardShell>
  );
}
