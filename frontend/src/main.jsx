import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";

import App from "./App.jsx";
import AuthPage from "./pages/AuthPage.jsx";
import SettingsPage from "./pages/SettingsPage.jsx";
import CampaignsPage from "./pages/CampaignsPage.jsx";
import AdsPage from "./pages/AdsPage.jsx";
import ExplorerPage from "./pages/ExplorerPage.jsx";
import LandingPage from "./pages/landingPage.jsx";
import DashboardMock from "./pages/DashboardMock.jsx";
import DocsPage from "./pages/DocsPage.jsx";
import { isLoggedIn } from "./api.js";

function ProtectedRoute({ children }) {
  return isLoggedIn() ? children : <Navigate to="/app/auth" replace />;
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/docs" element={<DocsPage />} />
        <Route path="/dashboard" element={<DashboardMock />} />
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
            path="campaigns"
            element={
              <ProtectedRoute>
                <CampaignsPage />
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
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
);
