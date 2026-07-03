export function ErrorBanner({ message }: { message: string | null }) {
  if (!message) {
    return null;
  }
  return <div className="error-banner">{message}</div>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <p className="loading">{label}</p>;
}
