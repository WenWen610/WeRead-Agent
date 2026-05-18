type Props = {
  active: boolean;
};

export function ToolCallIndicator({ active }: Props) {
  if (!active) return null;

  return (
    <article className="tool-call-indicator">
      <div className="tool-call-spinner" />
      <span className="tool-call-text">正在调用工具...</span>
    </article>
  );
}
