import Link from "next/link";
import type { ReactNode } from "react";

type SectionKey = "overview" | "usage" | "billing" | "keys" | "feedback";
type MetricTone = "usage" | "billing" | "keys" | "neutral" | "feedback";

type ActionLink = {
  href: string;
  label: string;
  variant?: "primary" | "secondary";
};

type DashboardShellProps = {
  active: SectionKey;
  title: string;
  description: string;
  stats?: Array<{
    label: string;
    value: string;
    detail: string;
    tone?: MetricTone;
  }>;
  actions?: ActionLink[];
  children: ReactNode;
};

const sections: Array<{ key: SectionKey; label: string; href: string }> = [
  { key: "overview", label: "Overview", href: "/" },
  { key: "usage", label: "Usage", href: "/usage" },
  { key: "billing", label: "Billing", href: "/billing" },
  { key: "keys", label: "API Keys", href: "/keys" },
  { key: "feedback", label: "Feedback", href: "/feedback" }
];

export function DashboardShell({
  active,
  title,
  description,
  stats = [],
  actions = [],
  children
}: DashboardShellProps) {
  return (
    <main className="dashboardShell">
      <header className="hero">
        <div className="heroCopy">
          <p className="eyebrow">Control Plane</p>
          <h1>{title}</h1>
          <p className="subhead">{description}</p>
          <div className="heroActions">
            {actions.map((action) => (
              <Link
                key={action.href}
                className={
                  action.variant === "secondary"
                    ? "secondaryButton"
                    : "primaryButton"
                }
                href={action.href}
              >
                {action.label}
              </Link>
            ))}
          </div>
        </div>

        {stats.length > 0 ? (
          <div className="heroMeta" aria-label="summary metrics">
            {stats.map((stat) => (
              <article key={stat.label} className={`metaCard ${stat.tone ?? "neutral"}`}>
                <p className="metaLabel">{stat.label}</p>
                <p className="metaValue">{stat.value}</p>
                <p className="metaDetail">{stat.detail}</p>
              </article>
            ))}
          </div>
        ) : null}
      </header>

      <nav className="sectionNav" aria-label="dashboard sections">
        {sections.map((section) => (
          <Link
            key={section.key}
            className={`sectionNavLink ${section.key === active ? "active" : ""}`}
            href={section.href}
            aria-current={section.key === active ? "page" : undefined}
          >
            {section.label}
          </Link>
        ))}
      </nav>

      {children}
    </main>
  );
}
