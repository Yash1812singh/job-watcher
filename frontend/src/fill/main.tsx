import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import FillApp from "./FillApp";
import "./fill.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <FillApp />
  </StrictMode>,
);
