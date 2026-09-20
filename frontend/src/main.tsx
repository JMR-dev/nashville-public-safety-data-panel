import "./styles.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app.tsx";

// index.html provides #root.
createRoot(document.querySelector("#root")!).render(
  <StrictMode>
    <App openEvents={(url) => new EventSource(url)} />
  </StrictMode>,
);
