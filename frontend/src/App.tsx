import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { AdminShell } from "./layouts/AdminShell";
import { DashboardPage } from "./pages/DashboardPage";
import { LogDetailPage } from "./pages/LogDetailPage";
import { LogsPage } from "./pages/LogsPage";
import { SessionPage } from "./pages/SessionPage";
import { TestPage } from "./pages/TestPage";
import { LoginPage } from "./pages/LoginPage";
import { ChatDebugPage } from "./pages/ChatDebugPage";
import { ConfigPage } from "./pages/ConfigPage";
import { isLoggedIn } from "./auth/adminAuth";

export default function App() {
  const location = useLocation();
  const loggedIn = isLoggedIn();

  if (!loggedIn && !location.pathname.startsWith("/login")) {
    return <Navigate to="/login" replace />;
  }

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<AdminShell />}>
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/logs" element={<LogsPage />} />
        <Route path="/logs/:runId" element={<LogDetailPage />} />
        <Route path="/sessions" element={<SessionPage />} />
        <Route path="/sessions/:sessionId" element={<SessionPage />} />
        <Route path="/test" element={<TestPage />} />
        <Route path="/chat" element={<ChatDebugPage />} />
        <Route path="/config" element={<ConfigPage />} />
      </Route>
      <Route path="*" element={<Navigate to={loggedIn ? "/dashboard" : "/login"} replace />} />
    </Routes>
  );
}
