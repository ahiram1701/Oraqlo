import Markdown from "react-markdown";

/** Markdown sin HTML crudo; se carga aparte (pesa ~160 KB) solo cuando hace falta. */
export default function MarkdownText({ children }: { children: string }) {
  return <Markdown>{children}</Markdown>;
}
