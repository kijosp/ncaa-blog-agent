import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const API = "";

interface Blog {
  url: string;
  platform: string;
  accessible: boolean;
  status_label: string;
  recency_status: string;
  last_post_date: string | null;
  rss_url: string | null;
  bookmarked?: boolean;
  bookmarked_at?: string;
  source?: string;
}

interface TeamEntry { sport: string; team: string; conference: string; blogs: Blog[]; }
type Registry = Record<string, TeamEntry>;

function getStatusStyle(blog: Blog) {
  if (blog.accessible && blog.recency_status === "active")
    return { border: "#22c55e", bg: "#f0fdf4", label: "Crawlable & Active", emoji: "✅", order: 0 };
  if (blog.accessible && blog.recency_status === "outdated")
    return { border: "#f59e0b", bg: "#fffbeb", label: "Crawlable but Outdated", emoji: "⚠️", order: 1 };
  if (blog.accessible)
    return { border: "#3b82f6", bg: "#eff6ff", label: "Crawlable (recency unknown)", emoji: "🔵", order: 2 };
  return { border: "#ef4444", bg: "#fef2f2", label: blog.status_label, emoji: "❌", order: 3 };
}

// ─── Searchable Team Select ──────────────────────────────────────────────────
function TeamSearch({ teams, value, onChange, placeholder }: { teams: string[]; value: string; onChange: (v: string) => void; placeholder?: string }) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => {
    if (!query) return teams;
    const q = query.toLowerCase();
    return teams.filter((t) => t.toLowerCase().includes(q));
  }, [teams, query]);

  useEffect(() => {
    const handler = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <input
        value={open ? query : value}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
        onFocus={() => { setOpen(true); setQuery(""); }}
        placeholder={placeholder || "Type to search teams..."}
        style={{ padding: "8px 12px", fontSize: 14, borderRadius: 6, border: "1px solid #ccc", width: "100%", minWidth: 180 }}
      />
      {open && (
        <div style={{ position: "absolute", top: "100%", left: 0, right: 0, maxHeight: 300, overflowY: "auto", background: "#fff", border: "1px solid #e5e7eb", borderRadius: 6, marginTop: 2, zIndex: 100, boxShadow: "0 4px 12px rgba(0,0,0,0.1)" }}>
          {filtered.slice(0, 365).map((t) => (
            <div key={t} onClick={() => { onChange(t); setQuery(""); setOpen(false); }} style={{ padding: "8px 12px", cursor: "pointer", fontSize: 13, borderBottom: "1px solid #f3f4f6" }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "#f0f9ff")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "#fff")}
            >{t}</div>
          ))}
          {filtered.length === 0 && <div style={{ padding: "8px 12px", color: "#9ca3af", fontSize: 13 }}>No teams found</div>}
        </div>
      )}
    </div>
  );
}

// ─── Toast ───────────────────────────────────────────────────────────────────
function Toast({ message, onClose }: { message: string; onClose: () => void }) {
  useEffect(() => { const t = setTimeout(onClose, 4000); return () => clearTimeout(t); }, [onClose]);
  return (
    <div style={{ position: "fixed", bottom: 24, right: 24, background: "#1f2937", color: "#fff", padding: "12px 20px", borderRadius: 8, fontSize: 14, zIndex: 1000, boxShadow: "0 4px 12px rgba(0,0,0,0.3)" }}>
      {message}
    </div>
  );
}

// ─── Star Button ─────────────────────────────────────────────────────────────
function StarButton({ bookmarked, onClick }: { bookmarked: boolean; onClick: () => void }) {
  return (
    <button onClick={(e) => { e.stopPropagation(); onClick(); }} title={bookmarked ? "Remove from favorites" : "Add to favorites"}
      style={{ background: "none", border: "none", cursor: "pointer", fontSize: 20, padding: "2px 6px", opacity: bookmarked ? 1 : 0.4, transition: "opacity 0.2s" }}>
      {bookmarked ? "⭐" : "☆"}
    </button>
  );
}

// ─── Blog Card ───────────────────────────────────────────────────────────────
function BlogCard({ blog, team, onBookmarkToggle, onDelete }: { blog: Blog; team: string; onBookmarkToggle: (t: string, u: string, b: boolean) => void; onDelete: (t: string, u: string) => void }) {
  const style = getStatusStyle(blog);
  const isPending = blog.status_label === "pending_check";
  return (
    <div style={{ border: "1px solid #e5e7eb", borderLeft: `5px solid ${isPending ? "#9ca3af" : style.border}`, backgroundColor: isPending ? "#f9fafb" : style.bg, padding: 14, marginBottom: 10, borderRadius: 8, display: "flex", alignItems: "flex-start", gap: 8 }}>
      <StarButton bookmarked={!!blog.bookmarked} onClick={() => onBookmarkToggle(team, blog.url, !blog.bookmarked)} />
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 14, fontWeight: 600 }}>
          <a href={blog.url} target="_blank" rel="noopener noreferrer" style={{ color: "#1d4ed8" }}>{blog.url}</a>
          {blog.source === "manual" && <span style={{ fontSize: 11, color: "#6b7280", marginLeft: 8 }}>(manually added)</span>}
          {isPending && <span style={{ fontSize: 11, color: "#9ca3af", marginLeft: 8 }}>⏳ checking...</span>}
        </div>
        <div style={{ marginTop: 5, fontSize: 13, color: "#374151" }}>
          {isPending ? "⏳ Pending check" : <>{style.emoji} {style.label}</>} &nbsp;|&nbsp; <strong>Platform:</strong> {blog.platform} &nbsp;|&nbsp; <strong>Last post:</strong> {blog.last_post_date || "N/A"}
        </div>
        {blog.rss_url && <div style={{ marginTop: 3, fontSize: 12 }}><strong>RSS:</strong> <a href={blog.rss_url} target="_blank" rel="noopener noreferrer" style={{ color: "#6366f1" }}>{blog.rss_url}</a></div>}
      </div>
      <button onClick={() => { if (confirm(`Remove ${blog.url} from ${team}?`)) onDelete(team, blog.url); }} title="Remove URL"
        style={{ background: "none", border: "1px solid #e5e7eb", borderRadius: 4, cursor: "pointer", fontSize: 12, color: "#dc2626", padding: "4px 8px", whiteSpace: "nowrap" }}>✕ Remove</button>
    </div>
  );
}

// ─── Add URL Form ────────────────────────────────────────────────────────────
function AddUrlForm({ teams, onAdd }: { teams: string[]; onAdd: (team: string, url: string) => Promise<void> }) {
  const [url, setUrl] = useState("");
  const [team, setTeam] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    if (!url || !team) return;
    setLoading(true);
    await onAdd(team, url);
    setUrl("");
    setLoading(false);
  };

  return (
    <div style={{ display: "flex", gap: 40, alignItems: "flex-end", marginBottom: 16, padding: 16, background: "#f9fafb", borderRadius: 8 }}>
      <label>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 3 }}>Team</div>
        <TeamSearch teams={teams} value={team} onChange={setTeam} placeholder="Search team..." />
      </label>
      <label style={{ flex: "0 1 350px" }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 3 }}>URL</div>
        <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/forum/basketball" style={{ width: "100%", padding: "8px 10px", fontSize: 13, borderRadius: 6, border: "1px solid #ccc" }} />
      </label>
      <button onClick={handleSubmit} disabled={loading || !url || !team} style={{ padding: "8px 16px", fontSize: 13, fontWeight: 600, borderRadius: 6, border: "none", background: loading ? "#9ca3af" : "#f59e0b", color: "#fff", cursor: loading ? "wait" : "pointer", whiteSpace: "nowrap" }}>
        {loading ? "Adding..." : "+ Add ⭐"}
      </button>
    </div>
  );
}

// ─── Summary View ────────────────────────────────────────────────────────────
function SummaryView({ registry }: { registry: Registry }) {
  const stats = useMemo(() => {
    const entries = Object.values(registry);
    const total = entries.length;
    if (total === 0) return null;
    let withCrawlable = 0, withCrawlableActive = 0, withoutCrawlable = 0, withoutCrawlableActive = 0;
    const errorCounts: Record<string, number> = {};
    for (const entry of entries) {
      const blogs = entry.blogs || [];
      if (blogs.some((b) => b.accessible)) withCrawlable++;
      if (blogs.some((b) => b.accessible && b.recency_status === "active")) withCrawlableActive++;
      if (!blogs.some((b) => b.accessible)) withoutCrawlable++;
      if (!blogs.some((b) => b.accessible && b.recency_status === "active")) withoutCrawlableActive++;
      // Collect errors across ALL teams (not just those without crawlable)
      for (const b of blogs) {
        if (!b.accessible) {
          const l = b.status_label || "unknown";
          errorCounts[l] = (errorCounts[l] || 0) + 1;
        }
      }
      if (blogs.length === 0) errorCounts["no_blogs_found"] = (errorCounts["no_blogs_found"] || 0) + 1;
    }
    return { total, withCrawlable, withCrawlableActive, withoutCrawlable, withoutCrawlableActive, errorCounts };
  }, [registry]);

  const teamsWithoutCrawlable = useMemo(() => {
    return Object.values(registry)
      .filter((e) => !(e.blogs || []).some((b) => b.accessible))
      .map((e) => e.team)
      .sort();
  }, [registry]);

  const teamsWithoutCrawlableActive = useMemo(() => {
    return Object.values(registry)
      .filter((e) => !(e.blogs || []).some((b) => b.accessible && b.recency_status === "active"))
      .map((e) => e.team)
      .sort();
  }, [registry]);

  if (!stats) return <p style={{ color: "#888" }}>No data in registry.</p>;
  const topErrors = Object.entries(stats.errorCounts).sort((a, b) => b[1] - a[1]).slice(0, 10);

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16, marginBottom: 32 }}>
        <StatCard label="Total Teams" value={stats.total} color="#6b7280" />
        <StatCard label="≥1 Crawlable URL" value={stats.withCrawlable} pct={Math.round(stats.withCrawlable * 100 / stats.total)} color="#3b82f6" />
        <StatCard label="≥1 Crawlable & Active" value={stats.withCrawlableActive} pct={Math.round(stats.withCrawlableActive * 100 / stats.total)} color="#22c55e" />
        <StatCard label="No Crawlable URL" value={stats.withoutCrawlable} pct={Math.round(stats.withoutCrawlable * 100 / stats.total)} color="#ef4444" />
      </div>

      {teamsWithoutCrawlable.length > 0 && (
        <div style={{ marginBottom: 24, background: "#fef2f2", borderRadius: 8, padding: 16, border: "1px solid #fecaca" }}>
          <h3 style={{ margin: "0 0 12px", fontSize: 14, color: "#991b1b" }}>🔴 Teams Without Any Crawlable URL ({teamsWithoutCrawlable.length})</h3>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {teamsWithoutCrawlable.map((t) => (
              <span key={t} style={{ fontSize: 12, background: "#fff", border: "1px solid #fca5a5", borderRadius: 4, padding: "4px 10px", color: "#dc2626" }}>{t}</span>
            ))}
          </div>
        </div>
      )}

      {teamsWithoutCrawlableActive.length > 0 && (
        <div style={{ marginBottom: 24, background: "#fffbeb", borderRadius: 8, padding: 16, border: "1px solid #fde68a" }}>
          <h3 style={{ margin: "0 0 12px", fontSize: 14, color: "#92400e" }}>🟡 Teams Without Any Crawlable & Active Fan Blog ({teamsWithoutCrawlableActive.length})</h3>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {teamsWithoutCrawlableActive.map((t) => (
              <span key={t} style={{ fontSize: 12, background: "#fff", border: "1px solid #fde68a", borderRadius: 4, padding: "4px 10px", color: "#92400e" }}>{t}</span>
            ))}
          </div>
        </div>
      )}

      {topErrors.length > 0 && (
        <div style={{ background: "#f9fafb", borderRadius: 8, padding: 16 }}>
          <h3 style={{ margin: "0 0 12px", fontSize: 14, color: "#374151" }}>Top Crawl Errors</h3>
          <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }}>
            <thead><tr style={{ borderBottom: "1px solid #e5e7eb" }}><th style={{ textAlign: "left", padding: "4px 8px" }}>Error</th><th style={{ textAlign: "right", padding: "4px 8px" }}>Count</th></tr></thead>
            <tbody>{topErrors.map(([e, c]) => <tr key={e} style={{ borderBottom: "1px solid #f3f4f6" }}><td style={{ padding: "4px 8px", fontFamily: "monospace" }}>{e}</td><td style={{ textAlign: "right", padding: "4px 8px" }}>{c}</td></tr>)}</tbody>
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

// ─── My Sources Tab ──────────────────────────────────────────────────────────
function MySourcesView({ registry, onBookmarkToggle }: { registry: Registry; onBookmarkToggle: (t: string, u: string, b: boolean) => void }) {
  const [filterTeam, setFilterTeam] = useState("");

  const bookmarked = useMemo(() => {
    const result: { team: string; blog: Blog }[] = [];
    for (const entry of Object.values(registry)) {
      for (const blog of entry.blogs || []) {
        if (blog.bookmarked) result.push({ team: entry.team, blog });
      }
    }
    return result.sort((a, b) => a.team.localeCompare(b.team));
  }, [registry]);

  const grouped = useMemo(() => {
    const map: Record<string, Blog[]> = {};
    for (const { team, blog } of bookmarked) {
      if (filterTeam && team !== filterTeam) continue;
      if (!map[team]) map[team] = [];
      map[team].push(blog);
    }
    return map;
  }, [bookmarked, filterTeam]);

  const bookmarkedTeams = useMemo(() => Array.from(new Set(bookmarked.map((b) => b.team))).sort(), [bookmarked]);

  if (bookmarked.length === 0) {
    return <p style={{ color: "#888", marginTop: 20 }}>No bookmarked sources yet. Click the ☆ on any blog card to add it to your favorites.</p>;
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 16 }}>
        <p style={{ fontSize: 13, color: "#6b7280", margin: 0 }}>{bookmarked.length} bookmarked source{bookmarked.length !== 1 ? "s" : ""} across {bookmarkedTeams.length} team{bookmarkedTeams.length !== 1 ? "s" : ""}</p>
        <select value={filterTeam} onChange={(e) => setFilterTeam(e.target.value)} style={{ padding: "6px 10px", fontSize: 13, borderRadius: 6, border: "1px solid #ccc" }}>
          <option value="">All Teams</option>
          {bookmarkedTeams.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
      </div>
      {Object.entries(grouped).map(([team, blogs]) => (
        <div key={team} style={{ marginBottom: 20 }}>
          <h3 style={{ fontSize: 14, fontWeight: 600, color: "#374151", marginBottom: 8, borderBottom: "1px solid #e5e7eb", paddingBottom: 4 }}>{team}</h3>
          {blogs.map((blog, i) => {
            const s = getStatusStyle(blog);
            return (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", marginBottom: 4, borderRadius: 6, background: s.bg, borderLeft: `4px solid ${s.border}` }}>
                <StarButton bookmarked={true} onClick={() => onBookmarkToggle(team, blog.url, false)} />
                <a href={blog.url} target="_blank" rel="noopener noreferrer" style={{ flex: 1, fontSize: 13, color: "#1d4ed8", textDecoration: "none" }}>{blog.url}</a>
                <span style={{ fontSize: 12, color: "#6b7280" }}>{blog.last_post_date || ""}</span>
                <span style={{ fontSize: 12 }}>{s.emoji}</span>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

// ─── Metric ──────────────────────────────────────────────────────────────────
function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div style={{ textAlign: "center" }}>
      <div style={{ fontSize: 24, fontWeight: 700 }}>{value}</div>
      <div style={{ fontSize: 11, color: "#6b7280" }}>{label}</div>
    </div>
  );
}

// ─── App ─────────────────────────────────────────────────────────────────────
export function App() {
  const [registry, setRegistry] = useState<Registry>({});
  const [tab, setTab] = useState<"summary" | "team" | "sources">("summary");
  const [sport, setSport] = useState("");
  const [team, setTeam] = useState("");
  const [toast, setToast] = useState("");

  const loadRegistry = useCallback(() => {
    fetch(`${API}/api/registry`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        console.log("Registry loaded:", Object.keys(data).length, "teams");
        setRegistry(data);
      })
      .catch((err) => {
        console.error("Failed to load registry:", err);
        setRegistry({});
      });
  }, []);

  useEffect(loadRegistry, [loadRegistry]);

  const sports = useMemo(() => Array.from(new Set(Object.values(registry).map((e) => e.sport))).sort(), [registry]);
  const teams = useMemo(() => Object.values(registry).filter((e) => !sport || e.sport === sport).map((e) => e.team).sort(), [registry, sport]);
  const selectedEntry = registry[team];
  const sortedBlogs = useMemo(() => selectedEntry ? [...selectedEntry.blogs].sort((a, b) => getStatusStyle(a).order - getStatusStyle(b).order) : [], [selectedEntry]);
  const bookmarkCount = useMemo(() => Object.values(registry).reduce((n, e) => n + (e.blogs || []).filter((b) => b.bookmarked).length, 0), [registry]);

  const handleBookmarkToggle = async (teamName: string, url: string, bookmarked: boolean) => {
    const res = await fetch(`${API}/api/bookmark`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ team: teamName, url, bookmarked }) });
    if (res.ok) { setToast(bookmarked ? "⭐ Added to favorites" : "Removed from favorites"); loadRegistry(); }
  };

  const handleAddUrl = async (teamName: string, url: string, force?: boolean) => {
    const res = await fetch(`${API}/api/add-url`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ team: teamName, url, force }) });
    const data = await res.json();
    if (data.conflict) { if (confirm(data.message)) await handleAddUrl(teamName, url, true); }
    else if (data.already_exists) { setToast(data.message); loadRegistry(); }
    else if (data.success) { setToast(data.message || "✅ URL added"); loadRegistry(); }
    else { setToast(`❌ ${data.error || "Failed to add"}`); }
  };

  const handleDelete = async (teamName: string, url: string) => {
    const res = await fetch(`${API}/api/delete-url`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ team: teamName, url }) });
    if (res.ok) { setToast("🗑️ URL removed"); loadRegistry(); }
  };

  return (
    <div style={{ fontFamily: "system-ui, sans-serif", maxWidth: 960, margin: "0 auto", padding: 24 }}>
      <h1 style={{ fontSize: 24, marginBottom: 16 }}>College Fan Blogs Discovery Dashboard</h1>

      <div style={{ display: "flex", gap: 0, marginBottom: 24, borderBottom: "2px solid #e5e7eb" }}>
        {(["summary", "team", "sources"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)} style={{ padding: "8px 20px", fontSize: 14, fontWeight: tab === t ? 600 : 400, border: "none", borderBottom: tab === t ? "2px solid #3b82f6" : "2px solid transparent", background: "none", cursor: "pointer", color: tab === t ? "#1d4ed8" : "#6b7280" }}>
            {t === "summary" ? "Summary" : t === "team" ? "Team View" : `⭐ My Sources (${bookmarkCount})`}
          </button>
        ))}
      </div>

      {tab === "summary" && <SummaryView registry={registry} />}
      {tab === "sources" && <MySourcesView registry={registry} onBookmarkToggle={handleBookmarkToggle} />}

      {tab === "team" && (
        <div>
          <div style={{ display: "flex", gap: 16, marginBottom: 12 }}>
            <label>
              <div style={{ fontWeight: 600, marginBottom: 4, fontSize: 13 }}>Sport</div>
              <select value={sport} onChange={(e) => { setSport(e.target.value); setTeam(""); }} style={{ padding: "8px 12px", fontSize: 14, borderRadius: 6, border: "1px solid #ccc", minWidth: 200 }}>
                <option value="">All Sports</option>
                {sports.map((s) => <option key={s} value={s}>{s.replace("_", " ").toUpperCase()}</option>)}
              </select>
            </label>
            <label>
              <div style={{ fontWeight: 600, marginBottom: 4, fontSize: 13 }}>Team</div>
              <TeamSearch teams={teams} value={team} onChange={setTeam} placeholder="Type to search teams..." />
            </label>
          </div>

          <AddUrlForm teams={teams} onAdd={handleAddUrl} />

          {selectedEntry && (
            <div>
              <div style={{ display: "flex", gap: 20, marginBottom: 16, padding: 12, background: "#f9fafb", borderRadius: 8 }}>
                <Metric label="Crawlable & Active" value={selectedEntry.blogs.filter((b) => b.accessible && b.recency_status === "active").length} />
                <Metric label="Total Crawlable" value={selectedEntry.blogs.filter((b) => b.accessible).length} />
                <Metric label="Total URLs" value={selectedEntry.blogs.length} />
                <Metric label="⭐ Bookmarked" value={selectedEntry.blogs.filter((b) => b.bookmarked).length} />
                <div style={{ marginLeft: "auto", fontSize: 13, color: "#555", alignSelf: "center" }}><strong>Conference:</strong> {selectedEntry.conference}</div>
              </div>
              {sortedBlogs.map((blog, i) => <BlogCard key={i} blog={blog} team={team} onBookmarkToggle={handleBookmarkToggle} onDelete={handleDelete} />)}
              {selectedEntry.blogs.length === 0 && <p style={{ color: "#888" }}>No blogs discovered for this team.</p>}
            </div>
          )}
          {!selectedEntry && !team && <p style={{ color: "#888", marginTop: 20 }}>Select a team to view blog discovery results.</p>}
        </div>
      )}

      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </div>
  );
}
