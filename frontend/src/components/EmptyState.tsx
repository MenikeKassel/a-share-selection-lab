import { FlaskConical } from "lucide-react";

export function EmptyState({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="empty-state">
      <FlaskConical size={24} />
      <strong>{title}</strong>
      <p>{description}</p>
    </div>
  );
}
