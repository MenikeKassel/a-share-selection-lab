import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Backtests } from "./pages/Backtests";
import { Dashboard } from "./pages/Dashboard";
import { FactorResearch } from "./pages/FactorResearch";
import { MLExperiments } from "./pages/MLExperiments";
import { MarketData } from "./pages/MarketData";
import { ResearchEngines } from "./pages/ResearchEngines";
import { WalkForward } from "./pages/WalkForward";

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="research-engines" element={<ResearchEngines />} />
        <Route path="market-data" element={<MarketData />} />
        <Route path="factor-research" element={<FactorResearch />} />
        <Route path="backtests" element={<Backtests />} />
        <Route path="ml-experiments" element={<MLExperiments />} />
        <Route path="walk-forward-experiments" element={<WalkForward />} />
      </Route>
    </Routes>
  );
}
