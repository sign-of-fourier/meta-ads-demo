import { useEffect, useState } from "react";
import { Link, Outlet, useNavigate } from "react-router-dom";
import { isLoggedIn, logout, getMe } from "./api.js";
import { UserContext } from "./UserContext.js";
import "./app.css";

export default function App() {
  const navigate = useNavigate();
  const loggedIn = isLoggedIn();
  const [user, setUser] = useState({ tier: "beta" });

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
          <span className="brand">
            <img src="/logo.svg" alt="AdStackers" style={{ height: 28 }} />
            <span className="wordmark">AdStackers</span>
          </span>
          {loggedIn && (
            <div className="nav-links">
              <Link to="/app/settings">Connect</Link>
              <Link to="/app/dashboard">Dashboard</Link>
              <Link to="/app/studio">Studio</Link>
              <span className={`tier-badge tier-${user.tier}`}>{user.tier === "free" ? "Free Tier" : user.tier}</span>
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
