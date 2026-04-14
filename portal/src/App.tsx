import { NavLink, Route, Routes } from "react-router-dom";
import ApplyPage from "./pages/ApplyPage";
import JobsPage from "./pages/JobsPage";
import QueuePage from "./pages/QueuePage";
import ReviewPage from "./pages/ReviewPage";
import ReviewDeskPage from "./pages/ReviewDeskPage";
import HistoryPage from "./pages/HistoryPage";
import SettingsPage from "./pages/SettingsPage";
import DriftPage from "./pages/DriftPage";
import SetupPage from "./pages/SetupPage";

const navItems = [
  { to: "/", label: "Jobs", end: true },
  { to: "/review-desk", label: "Review Desk" },
  { to: "/queue", label: "Queue" },
  { to: "/review", label: "Review" },
  { to: "/history", label: "History" },
  { to: "/drift", label: "Drift" },
  { to: "/setup", label: "Setup" },
];

export default function App() {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b bg-white">
        <nav className="mx-auto flex max-w-6xl gap-4 px-6 py-4">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `text-sm font-medium ${isActive ? "text-blue-600" : "text-slate-600 hover:text-slate-900"}`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-8">
        <Routes>
          <Route path="/" element={<JobsPage />} />
          <Route path="/review-desk" element={<ReviewDeskPage />} />
          <Route path="/queue" element={<QueuePage />} />
          <Route path="/apply/:applicationId" element={<ApplyPage />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/drift" element={<DriftPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/setup" element={<SetupPage />} />
        </Routes>
      </main>
    </div>
  );
}
