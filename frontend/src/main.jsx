import { createRoot } from "react-dom/client";
import ContractReviewApp from "./App";
import "./styles.css";

const container = document.getElementById("root");
const root = createRoot(container);

root.render(<ContractReviewApp />);
