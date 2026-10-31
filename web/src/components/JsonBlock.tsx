import { useState } from "react";

export function JsonBlock({ data }: { data: unknown }) {
  const [copied, setCopied] = useState(false);
  const text = typeof data === "string" ? data : JSON.stringify(data, null, 2);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // 剪贴板不可用时静默失败
    }
  }

  return (
    <div className="json-block">
      <button className="copy" onClick={copy}>
        {copied ? "已复制" : "复制"}
      </button>
      <pre>{text}</pre>
    </div>
  );
}
