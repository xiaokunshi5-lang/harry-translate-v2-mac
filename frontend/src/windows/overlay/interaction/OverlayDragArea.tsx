import type { ReactNode } from "react";
import { shouldStartOverlayDrag } from "./overlayContentDrag";
import { invokeOverlayStartDrag } from "./resizeIpc";

interface OverlayDragAreaProps {
  disabled?: boolean;
  onBrowserDragDelta?: (dx: number, dy: number) => void;
  children: ReactNode;
}

export function OverlayDragArea({
  disabled = false,
  onBrowserDragDelta,
  children,
}: OverlayDragAreaProps) {
  const handleMouseDown = (event: React.MouseEvent<HTMLDivElement>) => {
    if (disabled || !shouldStartOverlayDrag(event.target, event.button)) {
      return;
    }

    event.preventDefault();
    void invokeOverlayStartDrag();

    if (!onBrowserDragDelta) {
      return;
    }

    let lastX = event.screenX;
    let lastY = event.screenY;

    const handleMove = (moveEvent: MouseEvent) => {
      const dx = moveEvent.screenX - lastX;
      const dy = moveEvent.screenY - lastY;
      lastX = moveEvent.screenX;
      lastY = moveEvent.screenY;
      if (dx !== 0 || dy !== 0) {
        onBrowserDragDelta(dx, dy);
      }
    };

    const handleUp = () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseup", handleUp);
    };

    window.addEventListener("mousemove", handleMove);
    window.addEventListener("mouseup", handleUp);
  };

  return (
    <div
      className="overlay-drag-surface"
      data-tauri-drag-region={!disabled ? "" : undefined}
      onMouseDown={handleMouseDown}
    >
      {children}
    </div>
  );
}
