import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { LicenseGate } from "./LicenseGate";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <LicenseGate>
      <App />
    </LicenseGate>
  </React.StrictMode>,
);
