import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { signup, login, isLoggedIn } from "../api.js";

export default function AuthPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isSignup, setIsSignup] = useState(true);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  if (isLoggedIn()) {
    return <Navigate to="/app/settings" replace />;
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      if (isSignup) {
        await signup(email, password);
      } else {
        await login(email, password);
      }
      navigate("/app/settings");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-page">
      <h2>{isSignup ? "Sign Up" : "Log In"}</h2>
      <form onSubmit={handleSubmit} className="auth-form">
        <input
          type="email"
          placeholder="Email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <input
          type="password"
          placeholder="Password"
          required
          minLength={4}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <button type="submit" disabled={loading}>
          {loading ? "..." : isSignup ? "Sign Up" : "Log In"}
        </button>
        {error && <p className="error">{error}</p>}
      </form>
      <p className="toggle-auth">
        {isSignup ? "Already have an account?" : "Need an account?"}{" "}
        <button className="btn-link" onClick={() => setIsSignup(!isSignup)}>
          {isSignup ? "Log In" : "Sign Up"}
        </button>
      </p>
    </div>
  );
}
