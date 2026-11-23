import { ArrowLeftRight, BookmarkPlus, Plus, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import {
  addSessionGlossaryTerm,
  clearSessionGlossaryTerms,
  getSessionGlossaryTerms,
  promoteGlossaryTerm,
} from "../../shared/apiClient";
import type { GlossaryBucket, GlossaryEntry } from "../../shared/types";

interface DirectionOption {
  id: string;
  label: string;
  bucket: GlossaryBucket;
  langKey: string;
  sourcePlaceholder: string;
  targetPlaceholder: string;
}

const DIRECTION_OPTIONS: DirectionOption[] = [
  {
    id: "terms_trans:zh->en",
    label: "中→英",
    bucket: "terms_trans",
    langKey: "zh->en",
    sourcePlaceholder: "原词（例：黄仁勋 或 黄仁勋=Jason Huang）",
    targetPlaceholder: "目标词（例：Jason Huang）",
  },
  {
    id: "terms_trans:en->zh",
    label: "英→中",
    bucket: "terms_trans",
    langKey: "en->zh",
    sourcePlaceholder: "原词（例：NVIDIA）",
    targetPlaceholder: "目标词（例：英伟达）",
  },
  {
    id: "terms_trans:zh->fr",
    label: "中→法",
    bucket: "terms_trans",
    langKey: "zh->fr",
    sourcePlaceholder: "原词（例：人工智能）",
    targetPlaceholder: "目标词（例：Intelligence artificielle）",
  },
  {
    id: "terms_trans:fr->zh",
    label: "法→中",
    bucket: "terms_trans",
    langKey: "fr->zh",
    sourcePlaceholder: "原词（例：IA générative）",
    targetPlaceholder: "目标词（例：生成式人工智能）",
  },
  {
    id: "terms_trans:zh->ja",
    label: "中→日",
    bucket: "terms_trans",
    langKey: "zh->ja",
    sourcePlaceholder: "原词（例：大模型）",
    targetPlaceholder: "目标词（例：大規模言語モデル）",
  },
  {
    id: "terms_trans:ja->zh",
    label: "日→中",
    bucket: "terms_trans",
    langKey: "ja->zh",
    sourcePlaceholder: "原词（例：生成AI）",
    targetPlaceholder: "目标词（例：生成式人工智能）",
  },
  {
    id: "asr_fixes:zh",
    label: "中文识别纠偏",
    bucket: "asr_fixes",
    langKey: "zh",
    sourcePlaceholder: "误识词（例：落拉）",
    targetPlaceholder: "纠偏词（例：LoRA）",
  },
  {
    id: "asr_fixes:en",
    label: "英文识别纠偏",
    bucket: "asr_fixes",
    langKey: "en",
    sourcePlaceholder: "误识词（例：gen ai）",
    targetPlaceholder: "纠偏词（例：Generative AI）",
  },
  {
    id: "asr_fixes:fr",
    label: "法语识别纠偏",
    bucket: "asr_fixes",
    langKey: "fr",
    sourcePlaceholder: "误识词",
    targetPlaceholder: "纠偏词",
  },
  {
    id: "target_fixes:en",
    label: "英文译文润色",
    bucket: "target_fixes",
    langKey: "en",
    sourcePlaceholder: "待润色（例：Open AI）",
    targetPlaceholder: "规范词（例：OpenAI）",
  },
  {
    id: "target_fixes:zh",
    label: "中文译文润色",
    bucket: "target_fixes",
    langKey: "zh",
    sourcePlaceholder: "待润色（例：大语言模）",
    targetPlaceholder: "规范词（例：大语言模型）",
  },
];

function splitShorthand(rawSource: string, rawTarget: string): [string, string] {
  const trimmedSource = rawSource.trim();
  const trimmedTarget = rawTarget.trim();
  if (trimmedTarget) {
    return [trimmedSource, trimmedTarget];
  }
  for (const sep of ["=>", "->", "=", "：", ":"]) {
    const idx = trimmedSource.indexOf(sep);
    if (idx > 0) {
      return [
        trimmedSource.slice(0, idx).trim(),
        trimmedSource.slice(idx + sep.length).trim(),
      ];
    }
  }
  return [trimmedSource, trimmedTarget];
}

export interface QuickTermBarProps {
  defaultOpen?: boolean;
  open?: boolean;
  onOpenChange?: (next: boolean) => void;
  compact?: boolean;
  defaultOptionId?: string;
  onChanged?: () => void;
}

export function QuickTermBar({
  defaultOpen = true,
  open,
  onOpenChange,
  compact = false,
  defaultOptionId = "terms_trans:zh->en",
  onChanged,
}: QuickTermBarProps) {
  const [internalOpen, setInternalOpen] = useState(defaultOpen);
  const isOpen = open ?? internalOpen;
  const [optionId, setOptionId] = useState(defaultOptionId);
  const [source, setSource] = useState("");
  const [target, setTarget] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [sessionItems, setSessionItems] = useState<GlossaryEntry[]>([]);
  const sourceInputRef = useRef<HTMLInputElement>(null);
  const selectId = useId();

  const selectedOption =
    DIRECTION_OPTIONS.find((item) => item.id === optionId) ?? DIRECTION_OPTIONS[0];

  useEffect(() => {
    setOptionId(defaultOptionId);
  }, [defaultOptionId]);

  const setOpenState = useCallback(
    (next: boolean) => {
      if (open === undefined) {
        setInternalOpen(next);
      }
      onOpenChange?.(next);
    },
    [onOpenChange, open],
  );

  const refreshSession = useCallback(async () => {
    try {
      const response = await getSessionGlossaryTerms();
      setSessionItems(response.items);
    } catch {
      // Keep UI non-blocking if sidecar is starting up.
    }
  }, []);

  useEffect(() => {
    if (!isOpen || !compact) return;
    void refreshSession();
  }, [compact, isOpen, refreshSession]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.shiftKey && event.key.toLowerCase() === "g") {
        event.preventDefault();
        const next = !isOpen;
        setOpenState(next);
        if (next) {
          window.setTimeout(() => sourceInputRef.current?.focus(), 0);
        }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isOpen, setOpenState]);

  const handleSwap = () => {
    setSource(target);
    setTarget(source);
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const [resolvedSource, resolvedTarget] = splitShorthand(source, target);
    if (!resolvedSource || !resolvedTarget) {
      setError("请输入原词与目标词（支持 原词=目标词）");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const response = await addSessionGlossaryTerm({
        source: resolvedSource,
        target: resolvedTarget,
        bucket: selectedOption.bucket,
        lang_key: selectedOption.langKey,
      });
      setSessionItems(response.session_items);
      setSource("");
      setTarget("");
      setNotice(
        `已热生效 (${response.elapsed_ms.toFixed(1)} ms${
          response.pushed_channels.length > 0 ? " · 云端零断连同步" : ""
        })`,
      );
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "session_add_failed");
    } finally {
      setSubmitting(false);
    }
  };

  const handlePromote = async (item: GlossaryEntry) => {
    setError("");
    try {
      const response = await promoteGlossaryTerm({
        source: item.source,
        target: item.target,
        bucket: item.bucket || "terms_trans",
        lang_key: item.lang_key || "zh->en",
      });
      setSessionItems(response.session_items);
      setNotice(`已存为永久词：${item.source}`);
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "promote_failed");
    }
  };

  const handleClear = async () => {
    setError("");
    try {
      const response = await clearSessionGlossaryTerms();
      setSessionItems(response.session_items);
      setNotice("已清空本场临时词（未改磁盘）");
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "clear_failed");
    }
  };

  if (!isOpen) {
    return null;
  }

  return (
    <section
      className={`quick-term-bar${compact ? " quick-term-bar--compact" : ""}`}
      aria-label="快捷临时词会话注入条"
    >
      <div className="quick-term-bar__header">
        <span>
          <strong>随手快捷词条 (P0 本场生效)</strong>
          <small>全方向热注入 · ⌘⇧G 开关</small>
        </span>
        {onOpenChange || !defaultOpen ? (
          <button
            type="button"
            className="quick-term-bar__close"
            aria-label="收起快捷词条"
            onClick={() => setOpenState(false)}
          >
            <X size={15} />
          </button>
        ) : null}
      </div>

      <form className="quick-term-bar__form" onSubmit={(event) => void handleSubmit(event)}>
        <label className="quick-term-bar__direction" htmlFor={selectId}>
          <select
            id={selectId}
            aria-label="词条方向与类型"
            value={optionId}
            onChange={(event) => setOptionId(event.target.value)}
          >
            {DIRECTION_OPTIONS.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>

        <input
          ref={sourceInputRef}
          type="text"
          aria-label="原词"
          value={source}
          placeholder={selectedOption.sourcePlaceholder}
          onChange={(event) => setSource(event.target.value)}
        />

        <button
          type="button"
          className="quick-term-bar__swap"
          aria-label="互换原词与目标词"
          onClick={handleSwap}
        >
          <ArrowLeftRight size={14} />
        </button>

        <input
          type="text"
          aria-label="目标词"
          value={target}
          placeholder={selectedOption.targetPlaceholder}
          onChange={(event) => setTarget(event.target.value)}
        />

        <button
          type="submit"
          className="settings-button settings-button--primary"
          disabled={submitting}
        >
          <Plus size={15} />
          注入本场
        </button>
      </form>

      {notice || error ? (
        <div className={`quick-term-bar__feedback${error ? " is-error" : ""}`}>
          {error || notice}
        </div>
      ) : null}

      {compact && sessionItems.length > 0 ? (
        <div className="quick-term-bar__chips">
          {sessionItems.map((item) => (
            <span
              key={`${item.bucket}:${item.lang_key}:${item.source}`}
              className="quick-term-bar__chip"
            >
              <small>{item.lang_key || item.category}</small>
              <strong>
                {item.source} → {item.target}
              </strong>
              <button
                type="button"
                aria-label={`存为永久词 ${item.source}`}
                onClick={() => void handlePromote(item)}
              >
                <BookmarkPlus size={13} />
              </button>
            </span>
          ))}
          <button
            type="button"
            className="quick-term-bar__clear"
            onClick={() => void handleClear()}
          >
            <Trash2 size={13} />
            清空本场
          </button>
        </div>
      ) : null}
    </section>
  );
}
