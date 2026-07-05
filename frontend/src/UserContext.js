import { createContext, useContext } from "react";

export const UserContext = createContext({ tier: "beta" });
export const useUser = () => useContext(UserContext);

// Mirrors backend/permissions.py's WRITE_TIERS — kept here as the single frontend
// source of truth so buttons agree with what the backend will actually allow.
// This only controls whether we show/disable a button; the real boundary is the
// require_write_access dependency on the backend routes.
const WRITE_TIERS = new Set(["premium", "enterprise"]);
export const tierCanWrite = (tier) => WRITE_TIERS.has(tier);
