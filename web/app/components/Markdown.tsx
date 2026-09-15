import { createElement } from "react";
import ReactMarkdown from "react-markdown";
import { cn } from "@/lib/utils";

/** Which side of the markdown line the content falls on.
 *
 *  "prose"    what the model wrote for a human. Blockquotes are disabled and
 *             a leading `>` is escaped, because a citation heading path that
 *             lands at the start of a line would otherwise be eaten.
 *
 *  "document" a source document, shown so it can be read and judged. It is
 *             neither model prose nor a record of what the system did - it is
 *             the material itself, authored for a human to read, so it is
 *             rendered. A blockquote in it is a real blockquote and stays
 *             one, and nothing is escaped: escaping would shift every
 *             character offset and the passage highlight is anchored to
 *             those offsets. */
export type MarkdownVariant = "prose" | "document";

/** A character range in the SOURCE markdown, used to mark a passage. */
export type Highlight = { start: number; end: number };

/** What react-markdown hands a component override. `node.position` carries the
 *  offsets in the original source, which is what makes the highlight possible
 *  without touching the source text. */
type MarkedProps = {
  node?: { position?: { start?: { offset?: number }; end?: { offset?: number } } };
  className?: string;
  children?: React.ReactNode;
};

/** Block-level tags whose source range is compared against the highlight.
 *  Anything not listed renders normally and is simply never marked. */
const BLOCK_TAGS = [
  "p", "h1", "h2", "h3", "h4", "h5", "h6",
  "pre", "ul", "ol", "table", "blockquote", "hr",
] as const;

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
  variant = "prose",
  highlight,
}: {
  children: string;
  className?: string;
  variant?: MarkdownVariant;
  highlight?: Highlight | null;
}) {
  const isDocument = variant === "document";

  /** Highlighting inside rendered markdown, without splitting the source.
   *
   *  The passage is a range of the raw markdown, but after rendering that text
   *  is spread across elements and its syntax is gone, so it cannot be found
   *  in the output. Splitting the source into before/passage/after and
   *  rendering three documents would break any construct the boundary fell
   *  inside - a passage starting mid-list, or a fence split in half.
   *
   *  Instead the document is parsed once, as one document, and react-markdown
   *  hands each element the source offsets it came from. A block is marked
   *  when its range overlaps the passage. Marking is therefore block-granular:
   *  the highlight covers whole paragraphs and code blocks rather than a
   *  character range, which is also the honest unit - a passage is a run of
   *  blocks, not a substring of prose. */
  const components = highlight
    ? Object.fromEntries(
        BLOCK_TAGS.map((tag) => [
          tag,
          function Marked({ node, ...props }: MarkedProps) {
            const position = node?.position;
            const start = position?.start?.offset;
            const end = position?.end?.offset;
            const marked =
              typeof start === "number" &&
              typeof end === "number" &&
              start < highlight.end &&
              end > highlight.start;
            return createElement(tag, {
              ...props,
              ...(marked ? { "data-passage": "true" } : {}),
              className: cn(
                props.className,
                marked && "rounded bg-primary/10 px-2 -mx-2 ring-1 ring-primary/25",
              ),
            });
          },
        ]),
      )
    : undefined;

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
        isDocument &&
          "[&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground",
        className,
      )}
    >
      <ReactMarkdown
        disallowedElements={isDocument ? undefined : ["blockquote"]}
        unwrapDisallowed={!isDocument}
        components={components}
      >
        {isDocument ? children : keepLeadingAngleBrackets(children)}
      </ReactMarkdown>
    </div>
  );
}
