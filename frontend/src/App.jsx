import { Link, Outlet, useNavigate } from "react-router-dom";
import { isLoggedIn, logout } from "./api.js";
import "./app.css";

export default function App() {
  const navigate = useNavigate();
  const loggedIn = isLoggedIn();

  function handleLogout() {
    logout();
    navigate("/app/auth");
  }

  return (
    <div className="app">
      <nav className="navbar">
        <span className="brand">Meta Ads Demo</span>
        {loggedIn && (
          <div className="nav-links">
            <Link to="/app/settings">Settings</Link>
            <Link to="/app/campaigns">Campaigns</Link>
            <Link to="/app/ads">Ads</Link>
            <Link to="/app/explore">Explorer</Link>
            <button onClick={handleLogout} className="btn-link">
              Log out
            </button>
          </div>
        )}
      </nav>
      <main className="container">
        <Outlet />
      </main>
    </div>
  );
}
