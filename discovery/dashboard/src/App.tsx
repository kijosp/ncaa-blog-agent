import { useEffect, useMemo, useState } from "react";

interface Blog {
  url: string;
  platform: string;
  accessible: boolean;
  status_label: string;
  recency_status: string;
  last_post_date: string | null;
  rss_url: string | null;
}

interface TeamEntry {
  sport: string;
  team: string;
  conference: string;
  blogs: Blog[];
}

type Registry = Record<string, TeamEntry>;

function getStatusStyle(blog: Blog): { border: string; bg: string; label: string; emoji: string; order: number } {
  if (blog.accessible && blog.recency_status === "active") {
    return { border: "#22c55e", bg: "#f0fdf4", label: "Accessible & Active", emoji: "✅", order: 0 };
  }
  if (blog.accessible && blog.recency_status === "outdated") {
    return { border: "#f59e0b", bg: "#fffbeb", label: "Accessible but Outdated", emoji: "⚠️", order: 1 };
  }
  if (blog.accessible) {
    return { border: "#3b82f6", bg: "#eff6ff", label: "Accessible (recency unknown)", emoji: "🔵", order: 2 };
  }
  return { border: "#ef4444", bg: "#fef2f2", label: blog.status_label, emoji: "❌", order: 3 };
}

function SummaryView({ registry }: { registry: Registry }) {
  const stats = useMemo(() => {
    const entries = Object.values(registry);
    const total = entries.length;
    if (total === 0) return null;

    let withAccessible = 0;
    let withAccessibleActive = 0;
    let withoutAccessible = 0;
    const errorCounts: Record<string, number> = {};

    for (const entry of entries) {
      const blogs = entry.blogs || [];
      const hasAccessible = blogs.some((b) => b.accessible);
      const hasActive = blogs.some((b) => b.accessible && b.recency_status === "active");

      if (hasAccessible) withAccessible++;
      if (hasActive) withAccessibleActive++;
      if (!hasAccessible) {
        withoutAccessible++;
        if (blogs.length === 0) {
          errorCounts["no_blogs_found"] = (errorCounts["no_blogs_found"] || 0) + 1;
        } else {
          for (const b of blogs) {
            const label = b.status_label || "unknown";
            errorCounts[label] = (errorCounts[label] || 0) + 1;
          }
        }
      }
    }

    return { total, withAccessible, withAccessibleActive, withoutAccessible, errorCounts };
  }, [registry]);

  if (!stats) return <p style={{ color: "#888" }}>No data in registry.</p>;

  const topErrors = Object.entries(stats.errorCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10);

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16, marginBottom: 32 }}>
        <StatCard label="Total Teams" value={stats.total} color="#6b7280" />
        <StatCard label="≥1 Accessible URL" value={stats.withAccessible} pct={Math.round(stats.withAccessible * 100 / stats.total)} color="#3b82f6" />
        <StatCard label="≥1 Accessible & Active" value={stats.withAccessibleActive} pct={Math.round(stats.withAccessibleActive * 100 / stats.total)} color="#22c55e" />
        <StatCard label="No Accessible URL" value={stats.withoutAccessible} pct={Math.round(stats.withoutAccessible * 100 / stats.total)} color="#ef4444" />
      </div>

      {topErrors.length > 0 && (
        <div style={{ background: "#f9fafb", borderRadius: 8, padding: 16 }}>
          <h3 style={{ margin: "0 0 12px", fontSize: 14, color: "#374151" }}>Top Access Errors (teams without working URLs)</h3>
          <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid #e5e7eb" }}>
                <th style={{ textAlign: "left", padding: "4px 8px" }}>Error</th>
                <th style={{ textAlign: "right", padding: "4px 8px" }}>Count</th>
              </tr>
            </thead>
            <tbody>
              {topErrors.map(([error, count]) => (
                <tr key={error} style={{ borderBottom: "1px solid #f3f4f6" }}>
                  <td style={{ padding: "4px 8px", fontFamily: "monospace" }}>{error}</td>
                  <td style={{ textAlign: "right", padding: "4px 8px" }}>{count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value, pct, color }: { label: string; value: number; pct?: number; color: string }) {
  return (
    <div style={{ background: "#fff", border: "1px solid #e5e7eb", borderRadius: 8, padding: 16, textAlign: "center" }}>
      <div style={{ fontSize: 32, fontWeight: 700, color }}>{value}</div>
      {pct !== undefined && <div style={{ fontSize: 13, color: "#6b7280" }}>{pct}%</div>}
      <div style={{ fontSize: 12, color: "#6b7280", marginTop: 4 }}>{label}</div>
    </div>
  );
}

export function App() {
  const [registry, setRegistry] = useState<Registry>({});
  const [tab, setTab] = useState<"summary" | "team">("summary");
  const [sport, setSport] = useState("");
  const [team, setTeam] = useState("");

  useEffect(() => {
    fetch("http://localhost:8080/api/registry")
      .then((r) => r.json())
      .then((data: Registry) => setRegistry(data))
      .catch(() => setRegistry({}));
  }, []);

  const sports = useMemo(() => {
    const s = new Set<string>();
    Object.values(registry).forEach((e) => s.add(e.sport));
    return Array.from(s).sort();
  }, [registry]);

  const teams = useMemo(() => {
    return Object.values(registry)
      .filter((e) => !sport || e.sport === sport)
      .map((e) => e.team)
      .sort();
  }, [registry, sport]);

  const selectedEntry = registry[team];

  const sortedBlogs = useMemo(() => {
    if (!selectedEntry) return [];
    return [...selectedEntry.blogs].sort((a, b) => getStatusStyle(a).order - getStatusStyle(b).order);
  }, [selectedEntry]);

  return (
    <div style={{ fontFamily: "system-ui, sans-serif", maxWidth: 960, margin: "0 auto", padding: 24 }}>
      <h1 style={{ fontSize: 24, marginBottom: 16 }}>Blog Discovery Dashboard</h1>

      {/* Tabs */}
      <div style={{ display: "flex", gap: 0, marginBottom: 24, borderBottom: "2px solid #e5e7eb" }}>
        <button
          onClick={() => setTab("summary")}
          style={{ padding: "8px 20px", fontSize: 14, fontWeight: tab === "summary" ? 600 : 400, border: "none", borderBottom: tab === "summary" ? "2px solid #3b82f6" : "2px solid transparent", background: "none", cursor: "pointer", color: tab === "summary" ? "#1d4ed8" : "#6b7280" }}
        >
          Summary
        </button>
        <button
          onClick={() => setTab("team")}
          style={{ padding: "8px 20px", fontSize: 14, fontWeight: tab === "team" ? 600 : 400, border: "none", borderBottom: tab === "team" ? "2px solid #3b82f6" : "2px solid transparent", background: "none", cursor: "pointer", color: tab === "team" ? "#1d4ed8" : "#6b7280" }}
        >
          Team View
        </button>
      </div>

      {tab === "summary" && <SummaryView registry={registry} />}

      {tab === "team" && (
        <div>
          <div style={{ display: "flex", gap: 16, marginBottom: 16 }}>
            <label>
              <div style={{ fontWeight: 600, marginBottom: 4, fontSize: 13 }}>Sport</div>
              <select
                value={sport}
                onChange={(e) => { setSport(e.target.value); setTeam(""); }}
                style={{ padding: "8px 12px", fontSize: 14, borderRadius: 6, border: "1px solid #ccc", minWidth: 200 }}
              >
                <option value="">All Sports</option>
                {sports.map((s) => <option key={s} value={s}>{s.replace("_", " ").toUpperCase()}</option>)}
              </select>
            </label>

            <label>
              <div style={{ fontWeight: 600, marginBottom: 4, fontSize: 13 }}>Team</div>
              <select
                value={team}
                onChange={(e) => setTeam(e.target.value)}
                style={{ padding: "8px 12px", fontSize: 14, borderRadius: 6, border: "1px solid #ccc", minWidth: 300 }}
              >
                <option value="">Select a team...</option>
                {teams.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
          </div>

          {selectedEntry && (
            <div>
              {/* Metrics right under dropdown */}
              <div style={{ display: "flex", gap: 20, marginBottom: 16, padding: 12, background: "#f9fafb", borderRadius: 8 }}>
                <Metric label="Accessible & Active" value={selectedEntry.blogs.filter((b) => b.accessible && b.recency_status === "active").length} />
                <Metric label="Total Accessible" value={selectedEntry.blogs.filter((b) => b.accessible).length} />
                <Metric label="Total URLs" value={selectedEntry.blogs.length} />
                <div style={{ marginLeft: "auto", fontSize: 13, color: "#555", alignSelf: "center" }}>
                  <strong>Conference:</strong> {selectedEntry.conference}
                </div>
              </div>

              {selectedEntry.blogs.length === 0 && (
                <p style={{ color: "#888" }}>No blogs discovered for this team.</p>
              )}

              {/* Blog cards sorted by status */}
              {sortedBlogs.map((blog, i) => {
                const style = getStatusStyle(blog);
                return (
                  <div
                    key={i}
                    style={{
                      border: "1px solid #e5e7eb",
                      borderLeft: `5px solid ${style.border}`,
                      backgroundColor: style.bg,
                      padding: 14,
                      marginBottom: 10,
                      borderRadius: 8,
                    }}
                  >
                    <div style={{ fontSize: 14, fontWeight: 600 }}>
                      <a href={blog.url} target="_blank" rel="noopener noreferrer" style={{ color: "#1d4ed8" }}>
                        {blog.url}
                      </a>
                    </div>
                    <div style={{ marginTop: 5, fontSize: 13, color: "#374151" }}>
                      <span>{style.emoji} {style.label}</span>
                      &nbsp;&nbsp;|&nbsp;&nbsp;<strong>Platform:</strong> {blog.platform}
                      &nbsp;&nbsp;|&nbsp;&nbsp;<strong>Last post:</strong> {blog.last_post_date || "N/A"}
                    </div>
                    {blog.rss_url && (
                      <div style={{ marginTop: 3, fontSize: 12 }}>
                        <strong>RSS:</strong>{" "}
                        <a href={blog.rss_url} target="_blank" rel="noopener noreferrer" style={{ color: "#6366f1" }}>
                          {blog.rss_url}
                        </a>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {!selectedEntry && team === "" && (
            <p style={{ color: "#888" }}>Select a team to view blog discovery results.</p>
          )}
        </div>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div style={{ textAlign: "center" }}>
      <div style={{ fontSize: 24, fontWeight: 700 }}>{value}</div>
      <div style={{ fontSize: 11, color: "#6b7280" }}>{label}</div>
    </div>
  );
}
