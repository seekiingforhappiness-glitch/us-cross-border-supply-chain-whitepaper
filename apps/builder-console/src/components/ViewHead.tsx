export default function ViewHead({
  idx,
  total = 12,
  question,
  subtitle,
  kicker = "每个视图回答一个问题",
}: {
  idx: string;
  total?: number;
  question: string;
  subtitle: string;
  kicker?: string;
}) {
  return (
    <header className="view-head">
      <div className="view-eyebrow">
        <span className="idx">{idx} / {String(total).padStart(2, "0")}</span>
        <span className="rule" />
        <span className="eyebrow">{kicker}</span>
      </div>
      <h1 className="view-question">{question}</h1>
      <p className="view-subtitle">{subtitle}</p>
    </header>
  );
}
