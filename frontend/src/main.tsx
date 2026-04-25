import { ClerkProvider, useAuth } from "@clerk/react";
import { StrictMode, useEffect } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { setBearerTokenGetter } from "./lib/api";
import "./styles.css";

const clerkPublishableKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined;

function ClerkTokenBridge() {
  const { getToken, isLoaded } = useAuth();

  useEffect(() => {
    setBearerTokenGetter(async () => {
      if (!isLoaded) {
        return null;
      }
      return (await getToken()) ?? "local-dev";
    });
    return () => setBearerTokenGetter(null);
  }, [getToken, isLoaded]);

  return <App authMode="clerk" />;
}

function LocalDevApp() {
  useEffect(() => {
    setBearerTokenGetter(async () => "local-dev");
    return () => setBearerTokenGetter(null);
  }, []);

  return <App authMode="local-dev" />;
}

const root = createRoot(document.getElementById("root") as HTMLElement);

root.render(
  <StrictMode>
    {clerkPublishableKey ? (
      <ClerkProvider publishableKey={clerkPublishableKey}>
        <ClerkTokenBridge />
      </ClerkProvider>
    ) : (
      <LocalDevApp />
    )}
  </StrictMode>,
);
