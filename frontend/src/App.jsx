import { useEffect, useState } from "react";
import { Link, Outlet, useNavigate } from "react-router-dom";
import { isLoggedIn, logout, getMe } from "./api.js";
import { UserContext } from "./UserContext.js";
import "./app.css";

export default function App() {
  const navigate = useNavigate();
  const loggedIn = isLoggedIn();
  const [user, setUser] = useState({ tier: "free" });

  useEffect(() => {
    if (loggedIn) {
      getMe().then(setUser).catch(() => {});
    }
  }, [loggedIn]);

  function handleLogout() {
    logout();
    navigate("/app/auth");
  }

  return (
    <UserContext.Provider value={user}>
      <div className="app">
        <nav className="navbar">
          <span className="brand">Meta Ads Demo</span>
          {loggedIn && (
            <div className="nav-links">
              <Link to="/app/settings">Settings</Link>
              <Link to="/app/campaigns">Meta Ads</Link>
              <Link to="/app/google-campaigns">Google Ads</Link>
              <Link to="/app/ads">Ad Library</Link>
              <Link to="/app/explore">Explorer</Link>
              <span className={`tier-badge tier-${user.tier}`}>{user.tier}</span>
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
    </UserContext.Provider>
  );
}
