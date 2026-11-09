import { Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { getToken } from "./lib/api";
import { AttendancePage } from "./pages/Attendance";
import { DashboardPage } from "./pages/Dashboard";
import { LoginPage } from "./pages/Login";
import { AccessPage, CamerasPage, InsightsPage, ReportsPage } from "./pages/Operations";
import { OrganizationsPage } from "./pages/Organizations";
import { PeoplePage } from "./pages/People";
import { StructurePage } from "./pages/Structure";
import { UnknownsPage } from "./pages/Unknowns";

function Private() {
  if (!getToken()) return <Navigate to="/login" replace />;
  return <Shell />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<Private />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/organizations" element={<OrganizationsPage />} />
        <Route path="/people" element={<PeoplePage />} />
        <Route path="/structure" element={<StructurePage />} />
        <Route path="/attendance" element={<AttendancePage />} />
        <Route path="/unknown" element={<UnknownsPage />} />
        <Route path="/cameras" element={<CamerasPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/insights" element={<InsightsPage />} />
        <Route path="/access" element={<AccessPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
