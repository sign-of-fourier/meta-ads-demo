import { useEffect, useState } from "react";
import { adminListUsers, adminSetUserTier } from "../api.js";

const ADMIN_KEY_STORAGE = "adminApiKey";
const KNOWN_TIERS = ["free", "trial", "beta", "basic", "premium", "enterprise"];

function toDateInputValue(isoString) {
  if (!isoString) return "";
  // Accepts either a bare date or a full datetime; date input only wants YYYY-MM-DD.
  return isoString.slice(0, 10);
}

function UserRow({ user, adminKey, onSaved }) {
  const [tier, setTier] = useState(user.tier);
  const [expiresAt, setExpiresAt] = useState(toDateInputValue(user.tier_expires_at));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const dirty = tier !== user.tier || expiresAt !== toDateInputValue(user.tier_expires_at);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      const updated = await adminSetUserTier(adminKey, user.id, {
        tier,
        tierExpiresAt: expiresAt || null,
      });
      onSaved(updated);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <tr>
      <td>{user.email}</td>
      <td>
        <select value={tier} onChange={(e) => setTier(e.target.value)}>
          {KNOWN_TIERS.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
      </td>
      <td>
        <input
          type="date"
          value={expiresAt}
          onChange={(e) => setExpiresAt(e.target.value)}
        />
      </td>
      <td>{user.tier_source}</td>
      <td>{user.last_login_at || "never"}</td>
      <td>{user.login_count}</td>
      <td>{user.created_at}</td>
      <td>
        <button onClick={handleSave} disabled={!dirty || saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        {error && <div style={{ color: "crimson", fontSize: "0.85em" }}>{error}</div>}
      </td>
    </tr>
  );
}

function KeyPrompt({ onSubmit, error }) {
  const [value, setValue] = useState("");
  return (
    <div style={{ maxWidth: 420, margin: "4rem auto" }}>
      <h2>Admin key required</h2>
      <p>Paste the value of <code>ADMIN_API_KEY</code> from <code>backend/.env</code>.</p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit(value);
        }}
      >
        <input
          type="password"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Admin key"
          style={{ width: "100%", padding: "0.5rem" }}
          autoFocus
        />
        <button type="submit" style={{ marginTop: "0.5rem" }}>Continue</button>
      </form>
      {error && <p style={{ color: "crimson" }}>{error}</p>}
    </div>
  );
}

export default function AdminPage() {
  const [adminKey, setAdminKey] = useState(() => localStorage.getItem(ADMIN_KEY_STORAGE));
  const [users, setUsers] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  function loadUsers(key) {
    setLoading(true);
    setError(null);
    adminListUsers(key)
      .then((data) => {
        setUsers(data);
        localStorage.setItem(ADMIN_KEY_STORAGE, key);
        setAdminKey(key);
      })
      .catch((err) => {
        if (err.status === 401) {
          localStorage.removeItem(ADMIN_KEY_STORAGE);
          setAdminKey(null);
        }
        setError(err.message);
      })
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    if (adminKey) loadUsers(adminKey);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  function forgetKey() {
    localStorage.removeItem(ADMIN_KEY_STORAGE);
    setAdminKey(null);
    setUsers(null);
  }

  function handleRowSaved(updated) {
    setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
  }

  if (!adminKey || (!users && error)) {
    return <KeyPrompt onSubmit={loadUsers} error={error} />;
  }

  return (
    <div style={{ maxWidth: 1100, margin: "2rem auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2>Users</h2>
        <div>
          <button onClick={() => loadUsers(adminKey)} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>{" "}
          <button onClick={forgetKey}>Forget key</button>
        </div>
      </div>
      {loading && !users ? (
        <p>Loading…</p>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th align="left">Email</th>
              <th align="left">Tier</th>
              <th align="left">Expires</th>
              <th align="left">Source</th>
              <th align="left">Last login</th>
              <th align="left">Logins</th>
              <th align="left">Created</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {users?.map((u) => (
              <UserRow key={u.id} user={u} adminKey={adminKey} onSaved={handleRowSaved} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
