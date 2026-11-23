import { ArrowDown } from "lucide-react";
import {
  memo,
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  type RefObject,
  type WheelEvent as ReactWheelEvent,
} from "react";
import {
  splitAtStablePrefix,
  timelineRevision,
  type SubtitlePipelineState,
  type TimelineEntry,
} from "../../../lib/subtitles/subtitlePipeline";
import { IMMERSIVE_TEXT_SHADOW } from "../../../lib/subtitles/subtitleAppearance";
import type { UserSettings } from "../../../shared/types";
import {
  emphasisStyle,
  orderedSubtitleRoles,
  subtitleLaneBudget,
  timelineContentKey,
  timelineEntries,
  type LaneBudget,
  type SubtitleRole,
} from "./overlayModel";
import {
  TIMELINE_GLIDE_MS,
  isTimelineNearLive,
  rollupGlideShift,
  shouldShowReturnToLive,
} from "./timelineScroll";
import "./timeline.css";

const PATCH_FADE_MS = 150;
const LANE_OVERFLOW_TOLERANCE_PX = 2;
const PROGRAMMATIC_SCROLL_GUARD_MS = 100;

export function preserveBottomAppendAnchor(
  previousScrollTop: number,
): number {
  return Math.max(0, previousScrollTop);
}

interface TimelineProps {
  pipeline: SubtitlePipelineState;
  settings: UserSettings;
  immersive: boolean;
  browserFallback: boolean;
  immersiveTextShadow?: string;
}

function requestFrame(callback: FrameRequestCallback): number {
  if (typeof window.requestAnimationFrame === "function") {
    return window.requestAnimationFrame(callback);
  }
  return window.setTimeout(() => callback(performance.now()), 0);
}

function cancelFrame(handle: number): void {
  if (typeof window.cancelAnimationFrame === "function") {
    window.cancelAnimationFrame(handle);
  } else {
    window.clearTimeout(handle);
  }
}

export function useRollupGlide(updateKey: string, followLive: boolean) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const previousHeightRef = useRef(0);
  const previousTopRef = useRef(0);
  const programmaticScrollUntilRef = useRef(0);
  const programmaticScrollTopRef = useRef(0);

  useLayoutEffect(() => {
    const scroller = scrollRef.current;
    const content = contentRef.current;
    if (!scroller || !content) return;

    const previousHeight = previousHeightRef.current;
    const previousTop = previousTopRef.current;
    const nextHeight = content.scrollHeight;
    const nextTop = followLive
      ? scroller.scrollHeight
      : preserveBottomAppendAnchor(previousTop);
    scroller.scrollTop = nextTop;
    programmaticScrollTopRef.current = scroller.scrollTop;
    programmaticScrollUntilRef.current =
      performance.now() + PROGRAMMATIC_SCROLL_GUARD_MS;

    const shift = rollupGlideShift(
      previousHeight,
      nextHeight,
      scroller.clientHeight,
      followLive,
    );
    let frameHandle: number | null = null;
    if (shift > 0) {
      content.style.transition = "none";
      content.style.transform = `translateY(${shift}px)`;
      frameHandle = requestFrame(() => {
        content.style.transition = `transform ${TIMELINE_GLIDE_MS}ms ease-out`;
        content.style.transform = "translateY(0)";
        frameHandle = null;
      });
    } else if (content.style.transform) {
      content.style.transition = `transform ${TIMELINE_GLIDE_MS}ms ease-out`;
      content.style.transform = "translateY(0)";
    }

    previousHeightRef.current = nextHeight;
    previousTopRef.current = nextTop;
    return () => {
      if (frameHandle !== null) cancelFrame(frameHandle);
    };
  }, [followLive, updateKey]);

  const rememberScrollPosition = useCallback(() => {
    const node = scrollRef.current;
    if (node) previousTopRef.current = node.scrollTop;
  }, []);
  const isProgrammaticScroll = useCallback(
    (scrollTop: number) =>
      performance.now() < programmaticScrollUntilRef.current &&
      Math.abs(scrollTop - programmaticScrollTopRef.current) <= 1,
    [],
  );

  return {
    scrollRef,
    contentRef,
    rememberScrollPosition,
    isProgrammaticScroll,
  };
}

function LcpText({
  previous,
  current,
}: {
  previous: string;
  current: string;
}) {
  const segments = splitAtStablePrefix(previous, current);
  return (
    <>
      <span className="lcp-stable">{segments.stable}</span>
      <span className="lcp-tail">{segments.tail}</span>
    </>
  );
}

function roleText(
  entry: TimelineEntry,
  role: SubtitleRole,
): string {
  return role === "source" ? entry.source : entry.target;
}

function measuredElementHeight(node: HTMLElement): number {
  return Math.max(
    node.getBoundingClientRect().height,
    node.offsetHeight,
    node.scrollHeight,
  );
}

function useAvailableLaneHeight(
  scrollRef: { current: HTMLDivElement | null },
): number {
  const [availableHeight, setAvailableHeight] = useState(0);

  useLayoutEffect(() => {
    const scroller = scrollRef.current;
    if (!scroller) return;

    const measure = () => {
      const style = window.getComputedStyle(scroller);
      const padding =
        (Number.parseFloat(style.paddingTop) || 0) +
        (Number.parseFloat(style.paddingBottom) || 0);
      const nextHeight = Math.max(
        0,
        Math.round(scroller.clientHeight - padding),
      );
      setAvailableHeight((previous) =>
        previous === nextHeight ? previous : nextHeight,
      );
    };

    measure();
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() => measure());
    observer?.observe(scroller);
    window.addEventListener("resize", measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [scrollRef]);

  return availableHeight;
}

function useLaneHeight(
  enabled: boolean,
  lineHeightPx: number,
  maximumLines: number,
  contentKey: string,
  onHeightChange?: (height: number) => void,
) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLParagraphElement>(null);
  const [innerHeight, setInnerHeight] = useState(0);
  const maximumHeight =
    maximumLines > 0
      ? Math.max(1, Math.round(lineHeightPx * maximumLines))
      : 0;

  useLayoutEffect(() => {
    const inner = innerRef.current;
    if (!enabled || !inner) {
      setInnerHeight((previous) => (previous === 0 ? previous : 0));
      return;
    }

    const measure = () => {
      const nextHeight = measuredElementHeight(inner);
      if (nextHeight <= 0) return;
      setInnerHeight((previous) =>
        Math.abs(previous - nextHeight) < 0.5 ? previous : nextHeight,
      );
      onHeightChange?.(nextHeight);
    };

    measure();
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() => measure());
    observer?.observe(inner);
    window.addEventListener("resize", measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [contentKey, enabled, maximumHeight, onHeightChange]);

  return { viewportRef, innerRef, innerHeight, maximumHeight };
}

function useLaneRollupGlide(
  innerRef: { current: HTMLParagraphElement | null },
  followLive: boolean,
  innerHeight: number,
  maximumHeight: number,
) {
  const previousOverflowRef = useRef(0);

  useLayoutEffect(() => {
    const inner = innerRef.current;
    if (!inner) return;

    const nextOverflow = Math.max(0, innerHeight - maximumHeight);
    const shift = followLive
      ? Math.max(0, nextOverflow - previousOverflowRef.current)
      : 0;
    let frameHandle: number | null = null;

    if (shift > 0) {
      inner.style.transition = "none";
      inner.style.transform = `translateY(${shift}px)`;
      frameHandle = requestFrame(() => {
        inner.style.transition = `transform ${TIMELINE_GLIDE_MS}ms ease-out`;
        inner.style.transform = "translateY(0)";
        frameHandle = null;
      });
    } else if (!followLive) {
      inner.style.transition = "none";
      inner.style.transform = "";
    }

    previousOverflowRef.current = followLive ? nextOverflow : 0;
    return () => {
      if (frameHandle !== null) cancelFrame(frameHandle);
    };
  }, [followLive, innerHeight, innerRef, maximumHeight]);
}

function CompactLane({
  viewportRef,
  innerHeight,
  maximumHeight,
  overflowed,
  role,
  text,
  children,
}: {
  viewportRef: RefObject<HTMLDivElement | null>;
  innerHeight: number;
  maximumHeight: number;
  overflowed: boolean;
  role: SubtitleRole;
  text: string;
  children: ReactNode;
}) {
  return (
    <div
      ref={viewportRef}
      className={`overlay-lane-viewport${
        overflowed ? " overlay-lane-viewport--overflowed" : ""
      }`}
      data-lane-max-height={maximumHeight}
      data-lane-measured-height={Math.round(innerHeight)}
      data-lane-overflowed={overflowed ? "true" : "false"}
      data-lane-role={role}
      style={{
        maxHeight: maximumHeight,
        height: overflowed ? Math.min(innerHeight, maximumHeight) : undefined,
      }}
      aria-label={text}
    >
      {children}
    </div>
  );
}

function SubtitleLane({
  entry,
  role,
  settings,
  immersive,
  followLive,
  maximumLines,
  measureLane,
  measuredHeight,
  immersiveTextShadow,
  onHeightChange,
}: {
  entry: TimelineEntry;
  role: SubtitleRole;
  settings: UserSettings;
  immersive: boolean;
  followLive: boolean;
  maximumLines: number;
  measureLane: boolean;
  measuredHeight: number | null;
  immersiveTextShadow?: string;
  onHeightChange?: (height: number) => void;
}) {
  const source = role === "source";
  const text = roleText(entry, role);
  const lineHeightPx =
    (source ? settings.source_font_size : settings.target_font_size) *
    settings.line_height_multiplier;
  const contentKey = `${entry.patchVersion}:${text}`;
  const compact = followLive && maximumLines > 0;
  const { viewportRef, innerRef, innerHeight, maximumHeight } = useLaneHeight(
    compact && measureLane,
    lineHeightPx,
    maximumLines,
    contentKey,
    onHeightChange,
  );
  const effectiveInnerHeight =
    innerHeight > 0 ? innerHeight : measuredHeight ?? 0;
  const overflowed =
    compact &&
    maximumHeight > 0 &&
    effectiveInnerHeight > maximumHeight + LANE_OVERFLOW_TOLERANCE_PX;

  useLaneRollupGlide(
    innerRef,
    compact && followLive && measureLane,
    innerHeight,
    maximumHeight,
  );

  const line = (
    <p
      ref={innerRef}
      className={`overlay-subtitle-line overlay-subtitle-line--${role}`}
      data-lane-role={role}
      style={{
        ...emphasisStyle(role, settings.subtitle_emphasis),
        color: source
          ? settings.mic_subtitle_color
          : settings.subtitle_color,
        fontSize: `${
          source ? settings.source_font_size : settings.target_font_size
        }px`,
        lineHeight: settings.line_height_multiplier,
        textAlign: settings.subtitle_alignment,
        textShadow: immersive
          ? immersiveTextShadow ?? IMMERSIVE_TEXT_SHADOW
          : undefined,
        ...(overflowed
          ? {
              position: "absolute" as const,
              right: 0,
              bottom: 0,
              left: 0,
            }
          : {}),
      }}
    >
      <LcpText
        previous={source ? entry.previousSource : entry.previousTarget}
        current={text}
      />
    </p>
  );

  if (!compact) return line;

  return (
    <CompactLane
      viewportRef={viewportRef}
      innerHeight={effectiveInnerHeight}
      maximumHeight={maximumHeight}
      overflowed={overflowed}
      role={role}
      text={text}
    >
      {line}
    </CompactLane>
  );
}

function TimelineEntryCardComponent({
  entry,
  settings,
  immersive,
  showDivider,
  followLive,
  availableLaneHeight,
  measuredLanes,
  onMeasure,
  immersiveTextShadow,
}: {
  entry: TimelineEntry;
  settings: UserSettings;
  immersive: boolean;
  showDivider: boolean;
  followLive: boolean;
  availableLaneHeight: number;
  measuredLanes: { source: number; target: number } | null;
  onMeasure?: (measurement: {
    entryKey: string;
    source: number;
    target: number;
  }) => void;
  immersiveTextShadow?: string;
}) {
  const cardRef = useRef<HTMLElement>(null);
  const previousPatchVersionRef = useRef(entry.patchVersion);
  const previousHeightRef = useRef(0);
  const frameRef = useRef<number | null>(null);
  const sourceHeightRef = useRef(0);
  const targetHeightRef = useRef(0);
  const reportedLaneMeasurementRef = useRef("");
  const layoutKey = [
    settings.subtitle_display_mode,
    settings.source_font_size,
    settings.target_font_size,
    settings.subtitle_emphasis,
    settings.bilingual_layout_order,
    settings.line_height_multiplier,
    settings.subtitle_alignment,
    followLive ? "live" : "history",
    Math.round(availableLaneHeight),
  ].join(":");
  const previousLayoutKeyRef = useRef(layoutKey);
  const showSource = settings.subtitle_display_mode !== "translation";
  const showTarget = settings.subtitle_display_mode !== "original";
  const roles = orderedSubtitleRoles(settings.bilingual_layout_order).filter(
    (role) =>
      ((role === "source" && showSource) ||
        (role === "target" && showTarget)) &&
      roleText(entry, role).trim().length > 0,
  );
  const budget: LaneBudget = subtitleLaneBudget(
    settings.subtitle_display_mode,
    entry.source.trim().length > 0,
    entry.target.trim().length > 0,
    availableLaneHeight,
    settings.source_font_size,
    settings.target_font_size,
    settings.line_height_multiplier,
    measuredLanes,
  );

  const reportLaneMeasurement = useCallback(() => {
    if (!onMeasure) return;
    const measurement = {
      entryKey: entry.key,
      source: sourceHeightRef.current,
      target: targetHeightRef.current,
    };
    const signature = `${measurement.entryKey}:${measurement.source}:${measurement.target}`;
    if (reportedLaneMeasurementRef.current === signature) return;
    reportedLaneMeasurementRef.current = signature;
    onMeasure(measurement);
  }, [entry.key, onMeasure]);

  const measureSource = useCallback(
    (height: number) => {
      sourceHeightRef.current = height;
      reportLaneMeasurement();
    },
    [reportLaneMeasurement],
  );
  const measureTarget = useCallback(
    (height: number) => {
      targetHeightRef.current = height;
      reportLaneMeasurement();
    },
    [reportLaneMeasurement],
  );

  useLayoutEffect(() => {
    const card = cardRef.current;
    if (previousLayoutKeyRef.current === layoutKey) return;
    previousLayoutKeyRef.current = layoutKey;
    previousHeightRef.current = 0;
    if (card) card.style.minHeight = "";
  }, [layoutKey]);

  useLayoutEffect(() => {
    const card = cardRef.current;
    if (!card) return;

    const patched = previousPatchVersionRef.current !== entry.patchVersion;
    if (patched) {
      card.style.transition = `opacity ${PATCH_FADE_MS}ms ease`;
      card.style.opacity = "0.72";
      if (frameRef.current !== null) cancelFrame(frameRef.current);
      frameRef.current = requestFrame(() => {
        frameRef.current = null;
        card.style.opacity = "1";
      });
    }

    const measuredHeight = Math.max(
      card.getBoundingClientRect().height,
      card.offsetHeight,
      card.scrollHeight,
    );
    const lockedHeight = Math.max(previousHeightRef.current, measuredHeight);
    if (patched && lockedHeight > 0) {
      card.style.minHeight = `${Math.ceil(lockedHeight)}px`;
    }
    if (lockedHeight > 0) previousHeightRef.current = lockedHeight;
    previousPatchVersionRef.current = entry.patchVersion;
  }, [entry, entry.patchVersion, layoutKey, showDivider]);

  useLayoutEffect(
    () => () => {
      if (frameRef.current !== null) cancelFrame(frameRef.current);
    },
    [],
  );

  return (
    <article
      ref={cardRef}
      className={`overlay-timeline-entry${entry.final ? " is-final" : ""}${
        showDivider ? " has-divider" : ""
      }`}
      data-window-id="main"
      data-sent-id={entry.sentId ?? undefined}
      data-source-hash={entry.sourceHash || undefined}
      data-para-id={entry.paraId || undefined}
      data-final={entry.final ? "true" : "false"}
    >
      {roles.map((role) => (
        <SubtitleLane
          key={role}
          entry={entry}
          role={role}
          settings={settings}
          immersive={immersive}
          followLive={followLive}
          maximumLines={budget[role]}
          immersiveTextShadow={immersiveTextShadow}
          measureLane={onMeasure !== undefined}
          measuredHeight={
            role === "source"
              ? measuredLanes?.source ?? null
              : measuredLanes?.target ?? null
          }
          onHeightChange={
            onMeasure
              ? role === "source"
                ? measureSource
                : measureTarget
              : undefined
          }
        />
      ))}
    </article>
  );
}

const TimelineEntryCard = memo(TimelineEntryCardComponent);

export function Timeline({
  pipeline,
  settings,
  immersive,
  browserFallback,
  immersiveTextShadow,
}: TimelineProps) {
  const entries = timelineEntries(pipeline, "all");
  const updateKey = `${timelineContentKey(pipeline)}:${timelineRevision(pipeline)}`;
  const [followLive, setFollowLive] = useState(true);
  const [returnToLive, setReturnToLive] = useState(false);
  const [laneMeasurements, setLaneMeasurements] = useState<
    Record<string, { source: number; target: number }>
  >({});
  const {
    scrollRef,
    contentRef,
    rememberScrollPosition,
    isProgrammaticScroll,
  } = useRollupGlide(updateKey, followLive);
  const availableLaneHeight = useAvailableLaneHeight(scrollRef);
  const lastEntryKey = entries.at(-1)?.key ?? "";
  const previousLastEntryKeyRef = useRef(lastEntryKey);

  useLayoutEffect(() => {
    const previousKey = previousLastEntryKeyRef.current;
    if (previousKey === lastEntryKey) return;
    previousLastEntryKeyRef.current = lastEntryKey;
    setLaneMeasurements((previous) => {
      const next: Record<string, { source: number; target: number }> = {};
      if (previous[lastEntryKey]) next[lastEntryKey] = previous[lastEntryKey];
      if (previous[previousKey]) next[previousKey] = previous[previousKey];
      return next;
    });
  }, [lastEntryKey]);

  const handleLaneMeasure = useCallback(
    (measurement: { entryKey: string; source: number; target: number }) => {
      setLaneMeasurements((previous) => {
        const current = previous[measurement.entryKey];
        if (
          current &&
          Math.abs(current.source - measurement.source) < 0.5 &&
          Math.abs(current.target - measurement.target) < 0.5
        ) {
          return previous;
        }
        return {
          ...previous,
          [measurement.entryKey]: {
            source: measurement.source,
            target: measurement.target,
          },
        };
      });
    },
    [],
  );

  const handleScroll = () => {
    const node = scrollRef.current;
    if (!node) return;
    const metrics = {
      scrollTop: node.scrollTop,
      scrollHeight: node.scrollHeight,
      clientHeight: node.clientHeight,
    };
    rememberScrollPosition();
    if (isProgrammaticScroll(node.scrollTop)) return;
    const nearLive = isTimelineNearLive(metrics);
    setFollowLive(nearLive);
    setReturnToLive(shouldShowReturnToLive(metrics));
  };

  const enterHistoryReading = useCallback(() => {
    rememberScrollPosition();
    setFollowLive(false);
    setReturnToLive(true);
  }, [rememberScrollPosition]);

  const handleWheel = (event: ReactWheelEvent<HTMLDivElement>) => {
    if (event.deltaY < 0 && followLive) enterHistoryReading();
  };

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (
      followLive &&
      (event.key === "ArrowUp" ||
        event.key === "PageUp" ||
        event.key === "Home")
    ) {
      event.preventDefault();
      enterHistoryReading();
    }
  };

  const returnToLiveNow = () => {
    setFollowLive(true);
    setReturnToLive(false);
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  };

  return (
    <div className="overlay-timeline-shell">
      <div
        ref={scrollRef}
        className={`overlay-timeline${immersive ? " overlay-timeline--immersive" : ""}`}
        tabIndex={0}
        onScroll={handleScroll}
        onWheel={handleWheel}
        onKeyDown={handleKeyDown}
        aria-live="polite"
      >
        <div ref={contentRef} className="overlay-timeline__content">
          {entries.map((entry, index) => (
            <TimelineEntryCard
              key={entry.key}
              entry={entry}
              settings={settings}
              immersive={immersive}
              showDivider={
                settings.show_subtitle_dividers === true &&
                !immersive &&
                index < entries.length - 1
              }
              followLive={followLive}
              availableLaneHeight={availableLaneHeight}
              measuredLanes={laneMeasurements[entry.key] ?? null}
              onMeasure={
                index === entries.length - 1
                  ? handleLaneMeasure
                  : undefined
              }
              immersiveTextShadow={immersiveTextShadow}
            />
          ))}
        </div>
      </div>
      {returnToLive ? (
        browserFallback ? (
          <button
            type="button"
            className="timeline-return-live timeline-return-live--button"
            onClick={returnToLiveNow}
          >
            <ArrowDown size={13} />
            回到实时
          </button>
        ) : (
          <span className="timeline-return-live" aria-live="polite">
            已暂停跟随 · 下滑回到实时
          </span>
        )
      ) : null}
    </div>
  );
}
