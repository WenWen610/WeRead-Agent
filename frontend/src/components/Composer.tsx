type Props = {
  value: string;
  isSubmitting: boolean;
  onChange: (v: string) => void;
  onSend: () => void;
};

export function Composer({ value, isSubmitting, onChange, onSend }: Props) {
  return (
    <footer className="composer">
      <textarea
        placeholder="输入你要查询的内容"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            onSend();
          }
        }}
      />
      <button onClick={onSend} type="button" disabled={isSubmitting}>
        {isSubmitting ? "发送中..." : "发送"}
      </button>
    </footer>
  );
}
