import ReactMarkdown from "react-markdown";
import { cn } from "@/lib/utils";

/** Renders prose the model wrote for a human.
 *
 *  WHERE THIS IS USED, AND WHERE IT IS NOT
 *
 *  Rendered: the chat reply and the /ask answer. These are written to be read,
 *  and they arrive full of markdown - inline code, fenced blocks, bold - which
 *  was previously showing as literal backticks and asterisks. A documentation
 *  assistant that cannot display code is not much use.
 *
 *  Not rendered, deliberately:
 *
 *    heading paths      `api/README.md > api > CI > What CI cannot cover` is an
 *                       identifier you use to find a passage again. Its `>` is
 *                       a separator, not a blockquote, and its backticks are
 *                       not code. It is a string, shown as one.
 *    retrieved passages the trace shows what was SENT. Rendering doc source
 *                       would hide the actual bytes, turn `#` headings into
 *                       page titles inside a panel, and is the opposite of
 *                       what a trace is for.
 *    messages_sent      the literal prompt, for the same reason.
 *    raw_output         labelled "before any reformatting". Reformatting it
 *                       would make the label a lie.
 *    the user's message what they typed, echoed back.
 *
 *  The line: prose the model wrote for a human is rendered; anything that is a
 *  record of what actually passed through the system stays literal.
 *
 *  BLOCKQUOTES. A heading path that lands at the start of a line - `> api >
 *  CI` - is read by any markdown parser as a blockquote. Disallowing the
 *  element is not enough: by then the parser has already eaten the `>`, so the
 *  path renders a segment short. The marker is therefore escaped before
 *  parsing, which keeps the character, and the element is disallowed as well
 *  in case one arrives by another route. Nothing here has a legitimate use for
 *  a blockquote. */

/** Escape a `>` that begins a line, so it survives as a character instead of
 *  being consumed as blockquote syntax.
 *
 *  Fenced blocks are skipped: inside a fence the text is already literal, and
 *  escaping there would put a backslash into the code being shown. */
function keepLeadingAngleBrackets(markdown: string): string {
  let inFence = false;
  return markdown
    .split("\n")
    .map((line) => {
      if (/^\s*(```|~~~)/.test(line)) {
        inFence = !inFence;
        return line;
      }
      if (inFence) return line;
      return line.replace(/^(\s*)>/, "$1\\>");
    })
    .join("\n");
}
export default function Markdown({
  children,
  className,
}: {
  children: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "leading-7 [&>*+*]:mt-3",
        "[&_p]:leading-7",
        "[&_strong]:font-semibold [&_strong]:text-foreground",
        "[&_em]:italic",
        "[&_a]:text-primary [&_a]:underline [&_a]:underline-offset-2",
        "[&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:mt-1",
        "[&_h1]:text-lg [&_h2]:text-base [&_h3]:text-sm",
        "[&_h1]:font-semibold [&_h2]:font-semibold [&_h3]:font-semibold",
        "[&_code]:rounded [&_code]:bg-muted [&_code]:px-1.5 [&_code]:py-0.5",
        "[&_code]:font-mono [&_code]:text-[0.85em] [&_code]:text-primary",
        "[&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-background/70",
        "[&_pre]:p-3 [&_pre]:ring-1 [&_pre]:ring-border/60",
        // inside a fence the span should not repeat the inline-code chrome
        "[&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-foreground/85",
        "[&_table]:block [&_table]:overflow-x-auto [&_table]:text-sm",
        "[&_th]:border [&_th]:border-border [&_th]:px-2 [&_th]:py-1 [&_th]:text-left",
        "[&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1",
        className,
      )}
    >
      <ReactMarkdown disallowedElements={["blockquote"]} unwrapDisallowed>
        {keepLeadingAngleBrackets(children)}
      </ReactMarkdown>
    </div>
  );
}
