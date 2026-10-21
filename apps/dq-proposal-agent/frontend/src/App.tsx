import { Route, Routes } from "react-router-dom";
import RunDetails from "@/views/RunDetails";
import RunsList from "@/views/RunsList";

export default function App() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-border bg-background">
        <div className="max-w-7xl mx-auto px-6 py-3 flex items-center gap-4">
          <h1 className="font-semibold">DQ Proposal Agent</h1>
          <span className="text-xs text-muted-foreground">
            AI-driven data quality checks for Unity Catalog schemas
          </span>
        </div>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<RunsList />} />
          <Route path="/runs/:id" element={<RunDetails />} />
        </Routes>
      </main>
    </div>
  );
}
