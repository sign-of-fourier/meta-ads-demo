import { createContext, useContext } from "react";

export const UserContext = createContext({ tier: "free" });
export const useUser = () => useContext(UserContext);
