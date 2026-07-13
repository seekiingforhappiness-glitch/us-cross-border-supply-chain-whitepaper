export default function ViewHead({
  idx,
  question,
  subtitle,
}: {
  idx: string;
  question: string;
  subtitle: string;
}) {
  return (
    <header className="view-head">
      <div className="view-eyebrow">
        <span className="idx">{idx} / 06</span>
        <span className="rule" />
        <span className="eyebrow">每个视图回答一个问题</span>
      </div>
      <h1 className="view-question">{question}</h1>
      <p className="view-subtitle">{subtitle}</p>
    </header>
  );
}
