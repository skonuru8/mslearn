import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { AdminBar } from "./components/AdminBar";
import { ChatView } from "./views/ChatView";
import { ConceptView } from "./views/ConceptView";
import { CorpusView } from "./views/CorpusView";
import { CurriculumView } from "./views/CurriculumView";
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

export default function App() {
  return (
    <div className="app-shell">
      <AdminBar />
      <nav className="app-nav">
        <NavItem to="/corpus">Corpus</NavItem>
        <NavItem to="/curriculum">Curriculum</NavItem>
        <NavItem to="/quiz">Quiz</NavItem>
        <NavItem to="/chat">Chat</NavItem>
        <NavItem to="/memory">Memory</NavItem>
      </nav>
      <Routes>
        <Route path="/" element={<Navigate to="/corpus" replace />} />
        <Route path="/corpus" element={<CorpusView />} />
        <Route path="/curriculum" element={<CurriculumView />} />
        <Route path="/concepts/:id" element={<ConceptView />} />
        <Route path="/quiz" element={<QuizView />} />
        <Route path="/chat" element={<ChatView />} />
        <Route path="/memory" element={<MemoryView />} />
      </Routes>
    </div>
  );
}
