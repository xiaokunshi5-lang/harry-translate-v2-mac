import { MonitorScreens } from "./screens/MonitorScreens";
import { SettingsView } from "./screens/SettingsView";
import { SummaryWindow } from "./components/summary/SummaryWidget";
import { OverlayWindow } from "./windows/overlay/OverlayWindow";

export default function App() {
  const path = window.location.pathname.replace(/\/+$/, "") || "/";
  const search = new URLSearchParams(window.location.search);
  if (search.get("window") === "summary") {
    return <SummaryWindow />;
  }
  if (path === "/settings") {
    return <SettingsView />;
  }
  if (path === "/hud") {
    return <OverlayWindow />;
  }
  return <MonitorScreens />;
}
