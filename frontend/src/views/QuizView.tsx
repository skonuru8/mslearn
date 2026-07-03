import type { FormEvent } from "react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { QuizGrade, QuizNext, QuizStatRow } from "../api/types";
import { MarkdownWithCitations } from "../components/MarkdownWithCitations";
import { ErrorBanner, Loading } from "../components/Status";

const SESSION_KEY = "mslearn.quiz.session";

function quizSessionId(): string {
  const existing = sessionStorage.getItem(SESSION_KEY);
  if (existing) {
    return existing;
  }
  const created = crypto.randomUUID();
  sessionStorage.setItem(SESSION_KEY, created);
  return created;
}

export function QuizView() {
  const [quiz, setQuiz] = useState<QuizNext | null>(null);
  const [answer, setAnswer] = useState("");
  const [grade, setGrade] = useState<QuizGrade | null>(null);
  const [stats, setStats] = useState<QuizStatRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function loadNext() {
    setLoading(true);
    setGrade(null);
    setAnswer("");
    try {
      setQuiz(
        await api<QuizNext>(`/api/quiz/next?session_id=${encodeURIComponent(quizSessionId())}`),
      );
      setError(null);
    } catch (err) {
      setQuiz(null);
      setError(err instanceof Error ? err.message : "No quiz available");
    } finally {
      setLoading(false);
    }
  }

  async function loadStats() {
    try {
      setStats(await api<QuizStatRow[]>("/api/quiz/stats"));
    } catch {
      // stats are secondary
    }
  }

  useEffect(() => {
    void loadNext();
    void loadStats();
  }, []);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!quiz) {
      return;
    }
    try {
      const result = await api<QuizGrade>("/api/quiz/answer", {
        method: "POST",
        body: JSON.stringify({
          concept_id: quiz.concept_id,
          answer,
          session_id: quizSessionId(),
        }),
      });
      setGrade(result);
      await loadStats();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Grading failed");
    }
  }

  if (loading) {
    return <Loading />;
  }

  return (
    <section className="panel">
      <h1>Quiz</h1>
      <ErrorBanner message={error} />

      {quiz ? (
        <>
          <p>
            <strong>Concept:</strong> {quiz.concept_id}
          </p>
          <p>{quiz.question}</p>
          {!grade ? (
            <form className="form-grid" onSubmit={(event) => void onSubmit(event)}>
              <label>
                Your answer
                <textarea rows={5} value={answer} onChange={(event) => setAnswer(event.target.value)} required />
              </label>
              <button type="submit" className="primary">
                Submit
              </button>
            </form>
          ) : (
            <div className={`grade-card ${grade.correct ? "correct" : "incorrect"}`}>
              <p>
                <strong>{grade.correct ? "Correct" : "Incorrect"}</strong> · Score {grade.score_0_100}
              </p>
              <MarkdownWithCitations text={grade.explanation} />
              <button type="button" style={{ marginTop: "0.75rem" }} onClick={() => void loadNext()}>
                Next question
              </button>
            </div>
          )}
        </>
      ) : (
        <p className="empty-state">No quiz concepts available yet.</p>
      )}

      <h2>Stats</h2>
      <table>
        <thead>
          <tr>
            <th>Concept</th>
            <th>Attempts</th>
            <th>Avg score</th>
            <th>Last</th>
          </tr>
        </thead>
        <tbody>
          {stats.map((row) => (
            <tr key={row.concept_id}>
              <td>{row.concept_id}</td>
              <td>{row.attempts}</td>
              <td>{row.avg_score.toFixed(1)}</td>
              <td>{row.last_correct == null ? "—" : row.last_correct ? "✓" : "✗"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
