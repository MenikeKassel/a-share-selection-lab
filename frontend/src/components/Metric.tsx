interface Props {
  label: string;
  value: string;
  tone?: "default" | "positive" | "negative";
  note?: string;
}

export function Metric({ label, value, tone = "default", note }: Props) {
  return (
    <div className="metric">
      <span className="metric-label">{label}</span>
      <strong className={`metric-value metric-${tone}`}>{value}</strong>
      {note ? <small>{note}</small> : null}
    </div>
  );
}
