import { invokeOverlayResize, type ResizeEdge } from "./resizeIpc";

const EDGES: Array<{ edge: ResizeEdge; label: string }> = [
  { edge: "n", label: "上边缘缩放" },
  { edge: "s", label: "下边缘缩放" },
  { edge: "e", label: "右边缘缩放" },
  { edge: "w", label: "左边缘缩放" },
  { edge: "ne", label: "右上角缩放" },
  { edge: "nw", label: "左上角缩放" },
  { edge: "se", label: "右下角缩放" },
  { edge: "sw", label: "左下角缩放" },
];

interface ResizeHandlesProps {
  disabled?: boolean;
  onResizeDelta?: (edge: ResizeEdge, dx: number, dy: number) => void;
  onResizeCommit?: () => void;
}

export function ResizeHandles({
  disabled = false,
  onResizeDelta,
  onResizeCommit,
}: ResizeHandlesProps) {
  if (disabled) {
    return null;
  }

  const startResize = (
    edge: ResizeEdge,
    event: React.PointerEvent<HTMLDivElement>,
  ) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();

    const target = event.currentTarget;
    target.setPointerCapture(event.pointerId);
    let lastX = event.screenX;
    let lastY = event.screenY;

    const onMove = (moveEvent: PointerEvent) => {
      const dx = moveEvent.screenX - lastX;
      const dy = moveEvent.screenY - lastY;
      lastX = moveEvent.screenX;
      lastY = moveEvent.screenY;
      if (dx === 0 && dy === 0) return;
      onResizeDelta?.(edge, dx, dy);
      void invokeOverlayResize(edge, dx, dy);
    };

    const finish = () => {
      target.removeEventListener("pointermove", onMove);
      target.removeEventListener("pointerup", finish);
      target.removeEventListener("pointercancel", finish);
      if (target.hasPointerCapture(event.pointerId)) {
        target.releasePointerCapture(event.pointerId);
      }
      onResizeCommit?.();
    };

    target.addEventListener("pointermove", onMove);
    target.addEventListener("pointerup", finish);
    target.addEventListener("pointercancel", finish);
  };

  return (
    <>
      {EDGES.map(({ edge, label }) => (
        <div
          key={edge}
          data-resize-edge={edge}
          aria-label={label}
          className={`overlay-resize-handle overlay-resize-handle--${edge}`}
          onPointerDown={(event) => startResize(edge, event)}
        />
      ))}
    </>
  );
}
