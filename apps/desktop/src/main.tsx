import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { LicenseGate } from "./LicenseGate";
import { UiPreview } from "./UiPreview";
import "./App.css";

const preview = new URLSearchParams(window.location.search).get("ui") === "preview";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    {preview ? (
      <UiPreview />
    ) : (
      <LicenseGate>
        <App />
      </LicenseGate>
    )}
  </React.StrictMode>,
);
