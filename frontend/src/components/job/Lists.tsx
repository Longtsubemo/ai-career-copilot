import { Badge } from "../ui/Badge";
import type { Tone } from "../../utils/labels";

export function Chips({
  items,
  tone = "neutral",
  emptyText = "None listed",
}: {
  items: string[];
  tone?: Tone;
  emptyText?: string;
}) {
  if (!items.length) {
    return <p className="text-sm text-slate-500">{emptyText}</p>;
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((item) => (
        <Badge key={item} tone={tone}>
          {item}
        </Badge>
      ))}
    </div>
  );
}

export function BulletList({
  items,
  emptyText = "None listed",
}: {
  items: string[];
  emptyText?: string;
}) {
  if (!items.length) {
    return <p className="text-sm text-slate-500">{emptyText}</p>;
  }
  return (
    <ul className="space-y-1.5">
      {items.map((item) => (
        <li key={item} className="flex gap-2 text-sm text-slate-700">
          <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-300" />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

export function SectionTitle({ children }: { children: string }) {
  return (
    <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
      {children}
    </h4>
  );
}
