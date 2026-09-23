import { Navigate, Route, Routes } from "react-router-dom";
import { getToken } from "./api/client";
import Layout from "./components/Layout";
import { ToastProvider } from "./components/ui";
import Analytics from "./pages/Analytics";
import Anomalies from "./pages/Anomalies";
import Dashboard from "./pages/Dashboard";
import DeviceDetail from "./pages/DeviceDetail";
import Devices from "./pages/Devices";
import InspectionDetail from "./pages/InspectionDetail";
import Inspections from "./pages/Inspections";
import LiveInspection from "./pages/LiveInspection";
import Login from "./pages/Login";
import Maintenance from "./pages/Maintenance";
import Reports from "./pages/Reports";
import Settings from "./pages/Settings";
import Team from "./pages/Team";

function RequireAuth({ children }: { children: JSX.Element }) {
  return getToken() ? children : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <ToastProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          element={
            <RequireAuth>
              <Layout />
            </RequireAuth>
          }
        >
          <Route path="/" element={<Dashboard />} />
          <Route path="/live" element={<LiveInspection />} />
          <Route path="/devices" element={<Devices />} />
          <Route path="/devices/:id" element={<DeviceDetail />} />
          <Route path="/inspections" element={<Inspections />} />
          <Route path="/inspections/:id" element={<InspectionDetail />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/anomalies" element={<Anomalies />} />
          <Route path="/maintenance" element={<Maintenance />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/team" element={<Team />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </ToastProvider>
  );
}
