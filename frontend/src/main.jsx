import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";

import App from "./App.jsx";
import AuthPage from "./pages/AuthPage.jsx";
import SettingsPage from "./pages/SettingsPage.jsx";
import CampaignsPage from "./pages/CampaignsPage.jsx";
import GoogleCampaignsPage from "./pages/GoogleCampaignsPage.jsx";
import AdsPage from "./pages/AdsPage.jsx";
import ExplorerPage from "./pages/ExplorerPage.jsx";
import StudioPage from "./pages/StudioPage.jsx";
import DashboardPage from "./pages/DashboardPage.jsx";
import LandingPage from "./pages/landingPage.jsx";
import LandingPageV2 from "./pages/LandingPageV2.jsx";
import LandingPageAlt from "./pages/LandingPageAlt.jsx";
import DashboardMock from "./pages/DashboardMock.jsx";
import DocsPage from "./pages/DocsPage.jsx";
import GuidePage from "./pages/GuidePage.jsx";
import EvidencePage from "./pages/EvidencePage.jsx";
import AdminPage from "./pages/AdminPage.jsx";
import { isLoggedIn } from "./api.js";

function ProtectedRoute({ children }) {
  return isLoggedIn() ? children : <Navigate to="/app/auth" replace />;
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LandingPageV2 />} />
        <Route path="/b" element={<LandingPageAlt />} />
        <Route path="/landing-classic" element={<LandingPage />} />
        <Route path="/guide" element={<GuidePage />} />
        <Route path="/evidence" element={<EvidencePage />} />
        <Route path="/docs" element={<DocsPage />} />
        <Route path="/dashboard" element={<DashboardMock />} />
        {/* Internal only — gated by ADMIN_API_KEY, not linked from any nav */}
        <Route path="/admin" element={<AdminPage />} />
        <Route path="/app" element={<App />}>
          <Route index element={<Navigate to="/app/settings" replace />} />
          <Route path="auth" element={<AuthPage />} />
          <Route
            path="settings"
            element={
              <ProtectedRoute>
                <SettingsPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="dashboard"
            element={
              <ProtectedRoute>
                <DashboardPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="campaigns"
            element={
              <ProtectedRoute>
                <CampaignsPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="google-campaigns"
            element={
              <ProtectedRoute>
                <GoogleCampaignsPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="ads"
            element={
              <ProtectedRoute>
                <AdsPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="explore"
            element={
              <ProtectedRoute>
                <ExplorerPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="studio"
            element={
              <ProtectedRoute>
                <StudioPage />
              </ProtectedRoute>
            }
          />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
);
