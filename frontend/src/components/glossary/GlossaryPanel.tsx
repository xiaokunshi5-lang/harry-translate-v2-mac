import {
  BookmarkPlus,
  BookOpenText,
  ChevronLeft,
  ChevronRight,
  LoaderCircle,
  Lock,
  Search,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  clearSessionGlossaryTerms,
  getGlossaryPacks,
  getGlossaryTerms,
  getSessionGlossaryTerms,
  promoteGlossaryTerm,
  toggleGlossaryPack,
} from "../../shared/apiClient";
import type { GlossaryEntry, GlossaryPack, GlossaryQuota } from "../../shared/types";
import { SettingsSection } from "../settings/Controls";
import { QuickTermBar } from "./QuickTermBar";

type Category =
  | "all"
  | "terms_zh_to_en"
  | "terms_en_to_zh"
  | "asr_fixes"
  | "target_fixes";

const CATEGORIES: Array<{ id: Category; label: string }> = [
  { id: "all", label: "全部" },
  { id: "terms_zh_to_en", label: "中→英" },
  { id: "terms_en_to_zh", label: "英→中" },
  { id: "asr_fixes", label: "ASR 纠偏" },
  { id: "target_fixes", label: "译文润色" },
];

export function GlossaryPanel() {
  const [packs, setPacks] = useState<GlossaryPack[]>([]);
  const [quota, setQuota] = useState<GlossaryQuota>({
    cloud_cap: 250,
    active_terms: 0,
    local_terms: 0,
    provider_limit: 1000,
    session_terms: 0,
    wire_bytes: 0,
    wire_budget_bytes: 4096,
  });
  const [loadingStage, setLoadingStage] = useState("all_packs_ready");
  const [category, setCategory] = useState<Category>("all");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [items, setItems] = useState<GlossaryEntry[]>([]);
  const [sessionItems, setSessionItems] = useState<GlossaryEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [packResponse, termResponse, sessionResponse] = await Promise.all([
        getGlossaryPacks(),
        getGlossaryTerms({ category, q: query, limit: 100, offset: page * 100 }),
        getSessionGlossaryTerms(),
      ]);
      setPacks(packResponse.packs);
      setQuota(packResponse.quota);
      setLoadingStage(packResponse.loading_stage);
      setItems(termResponse.items);
      setTotal(termResponse.total);
      setCounts(termResponse.counts_by_category);
      setSessionItems(sessionResponse.items);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "glossary_request_failed");
    } finally {
      setLoading(false);
    }
  }, [category, page, query]);

  useEffect(() => {
    void load();
  }, [load]);

  const corePacks = useMemo(() => packs.filter((pack) => pack.priority <= 8), [packs]);
  const extendedPacks = useMemo(() => packs.filter((pack) => pack.priority > 8), [packs]);

  const onToggle = async (pack: GlossaryPack) => {
    if (pack.locked) return;
    try {
      const response = await toggleGlossaryPack(pack.id, !pack.enabled);
      setPacks(response.packs);
      setQuota(response.quota);
      setLoadingStage(response.loading_stage);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "toggle_failed");
    }
  };

  const onPromote = async (entry: GlossaryEntry) => {
    try {
      await promoteGlossaryTerm({
        source: entry.source,
        target: entry.target,
        bucket: entry.bucket || "terms_trans",
        lang_key: entry.lang_key || "zh->en",
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "promote_failed");
    }
  };

  const onClearSession = async () => {
    try {
      await clearSessionGlossaryTerms();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "clear_session_failed");
    }
  };

  return (
    <>
      <SettingsSection
        title="快捷临时词会话注入"
        description="支持全方向即时热更新本地区间锁自动机与云端会话语料，字幕画面保持纯净无标红。"
      >
        <div className="glossary-quickbar-wrap">
          <QuickTermBar defaultOpen onChanged={() => void load()} />
        </div>
      </SettingsSection>

      <SettingsSection
        title="本场临时词 (P0)"
        description="仅在当前会话生效，可单条升格为永久自定义词 (P1)，或会后一键清空（零写盘）。"
      >
        <div className="glossary-session-toolbar">
          <small>当前本场词条：{sessionItems.length} 条</small>
          <button
            type="button"
            className="settings-button"
            disabled={sessionItems.length === 0}
            onClick={() => void onClearSession()}
          >
            <Trash2 size={14} />
            清空本场临时词
          </button>
        </div>
        {sessionItems.length === 0 ? (
          <p className="glossary-empty-hint">暂无本场临时词，可在上方注入条随手添加。</p>
        ) : (
          <ul className="glossary-session-list">
            {sessionItems.map((entry) => (
              <li
                key={`${entry.bucket}:${entry.lang_key}:${entry.source}`}
                className="glossary-session-row"
              >
                <span className="glossary-session-row__meta">
                  <em>{entry.lang_key || entry.category}</em>
                  <strong>
                    {entry.source} → {entry.target}
                  </strong>
                </span>
                <button
                  type="button"
                  className="settings-button"
                  onClick={() => void onPromote(entry)}
                >
                  <BookmarkPlus size={14} />
                  存为永久词
                </button>
              </li>
            ))}
          </ul>
        )}
      </SettingsSection>

      <SettingsSection title="会议词库" description="锁定应急包与日常精选包同步装载，云端只发送预算内词条。">
        <div className="settings-feature-card">
          <BookOpenText size={21} />
          <span>
            <strong>250 条精选常驻词与 4 KB 线预算</strong>
            <small>
              本地 {quota.local_terms.toLocaleString()} / 活跃 {quota.active_terms.toLocaleString()} / 上限{" "}
              {quota.cloud_cap} / 线载荷 {quota.wire_bytes} B
            </small>
          </span>
          <em>{loadingStage === "all_packs_ready" ? "已挂载" : "装载中"}</em>
        </div>
      </SettingsSection>

      <SettingsSection title="词包管理" description="00 基础包锁定，01 为日常高频精选包。">
        <div className="glossary-pack-grid">
          {[...corePacks, ...extendedPacks].map((pack) => (
            <label key={pack.id} className="glossary-pack-card">
              <input
                type="checkbox"
                checked={pack.enabled}
                disabled={pack.locked}
                onChange={() => void onToggle(pack)}
              />
              <span>
                <strong>
                  {pack.locked ? <Lock size={14} /> : null}
                  {pack.name}
                </strong>
                <small>{pack.terms_count.toLocaleString()} 词</small>
              </span>
            </label>
          ))}
        </div>
      </SettingsSection>

      <SettingsSection title="词条明细" description="三表物理隔离与单遍区间锁匹配，分页请求单页 100 条。">
        <div className="glossary-category-tabs">
          {CATEGORIES.map((item) => {
            const countValue =
              item.id === "all" ? quota.active_terms : (counts[item.id] ?? 0);
            return (
              <button
                key={item.id}
                type="button"
                className={category === item.id ? "is-selected" : ""}
                onClick={() => {
                  setCategory(item.id);
                  setPage(0);
                }}
              >
                {item.label}
                {countValue > 0 ? ` (${countValue.toLocaleString()})` : ""}
              </button>
            );
          })}
        </div>
        <div className="glossary-search-row">
          <Search size={16} />
          <input
            type="search"
            value={query}
            placeholder="搜索中英文词条"
            onChange={(event) => {
              setQuery(event.target.value);
              setPage(0);
            }}
          />
        </div>
        {loading ? (
          <div className="glossary-loading">
            <LoaderCircle size={18} className="spin" /> 正在读取词条
          </div>
        ) : (
          <ul className="glossary-term-list">
            {items.map((entry) => (
              <li
                key={`${entry.priority}:${entry.bucket || entry.category}:${entry.lang_key || ""}:${entry.source}`}
                className="glossary-term-row"
              >
                <span>
                  <strong>{entry.source}</strong> → {entry.target}
                </span>
                <small>
                  P{entry.priority} · {entry.lang_key || entry.category}
                </small>
              </li>
            ))}
          </ul>
        )}
        <div className="glossary-pagination">
          <button
            type="button"
            disabled={page === 0}
            onClick={() => setPage((value) => value - 1)}
          >
            <ChevronLeft size={16} /> 上一页
          </button>
          <span>
            {page + 1} / {Math.max(1, Math.ceil(total / 100))}
          </span>
          <button
            type="button"
            disabled={(page + 1) * 100 >= total}
            onClick={() => setPage((value) => value + 1)}
          >
            下一页 <ChevronRight size={16} />
          </button>
        </div>
      </SettingsSection>
      {error ? <div className="settings-error">{error}</div> : null}
    </>
  );
}
