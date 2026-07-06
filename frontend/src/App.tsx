import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { AdminBar } from "./components/AdminBar";
import { ProjectSwitcher } from "./components/ProjectSwitcher";
import { useProject } from "./context/ProjectContext";
import { ChatView } from "./views/ChatView";
import { ConceptView } from "./views/ConceptView";
import { CorpusView } from "./views/CorpusView";
import { CurriculumView } from "./views/CurriculumView";
import { EvalsView } from "./views/EvalsView";
import { MemoryView } from "./views/MemoryView";
import { QuizView } from "./views/QuizView";
import "./app.css";

function NavItem({ to, children }: { to: string; children: string }) {
  return (
    <NavLink to={to} className={({ isActive }) => (isActive ? "active" : undefined)}>
      {children}
    </NavLink>
  );
}

function AppRoutes() {
  const { projectId } = useProject();

  return (
    <Routes key={projectId}>
      <Route path="/" element={<Navigate to="/corpus" replace />} />
      <Route path="/corpus" element={<CorpusView />} />
      <Route path="/curriculum" element={<CurriculumView />} />
      <Route path="/concepts/:id" element={<ConceptView />} />
      <Route path="/quiz" element={<QuizView />} />
      <Route path="/chat" element={<ChatView />} />
      <Route path="/memory" element={<MemoryView />} />
      <Route path="/evals" element={<EvalsView />} />
    </Routes>
  );
}

export default function App() {
  return (
    <div className="app-shell">
      <AdminBar />
      <nav className="app-nav" aria-label="Main">
        <ProjectSwitcher />
        <NavItem to="/corpus">My materials</NavItem>
        <NavItem to="/curriculum">My course</NavItem>
        <NavItem to="/quiz">Quiz</NavItem>
        <NavItem to="/chat">Ask questions</NavItem>
        <NavItem to="/memory">What the app knows about me</NavItem>
      </nav>
      <AppRoutes />
    </div>
  );
}
