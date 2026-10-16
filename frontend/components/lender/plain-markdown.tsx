import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Shows the small Markdown subset the guideline sections and decision records use as plain React elements:
 * headings, "- " lists (a list item may continue on indented lines), pipe tables, "> " quotes, ``` fenced blocks
 * (line breaks kept), `inline code` and paragraphs. No HTML from the API is ever rendered: every piece of text goes
 * in as a React text node, so a section or record cannot inject markup. Anything else is shown as plain text.
 */

type Block =
  | { kind: "heading"; text: string }
  | { kind: "list"; items: string[] }
  | { kind: "table"; header: string[]; rows: string[][] }
  | { kind: "quote"; text: string }
  | { kind: "code"; text: string }
  | { kind: "paragraph"; text: string };

function cells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function isSeparator(line: string): boolean {
  return /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/.test(line.trim());
}

const LIST_ITEM = /^[-*]\s/;
const HEADING = /^#{1,6}\s/;
const FENCE = /^```/;

/** True when a trimmed line starts a block of its own (so it cannot continue a paragraph). */
function startsBlock(trimmed: string): boolean {
  return trimmed === "" || trimmed.startsWith("|") || trimmed.startsWith(">") || HEADING.test(trimmed) || LIST_ITEM.test(trimmed) || FENCE.test(trimmed);
}

/** Split the text into blocks, line by line. */
export function parseBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  let index = 0;
  const lineAt = (at: number): string => lines[at] ?? "";
  while (index < lines.length) {
    const line = lineAt(index);
    const trimmed = line.trim();
    if (trimmed === "") {
      index += 1;
    } else if (FENCE.test(trimmed)) {
      const body: string[] = [];
      index += 1;
      while (index < lines.length && !FENCE.test(lineAt(index).trim())) {
        body.push(lineAt(index));
        index += 1;
      }
      index += 1; // the closing fence (or the end of the text)
      blocks.push({ kind: "code", text: body.join("\n") });
    } else if (HEADING.test(trimmed)) {
      blocks.push({ kind: "heading", text: trimmed.replace(/^#{1,6}\s+/, "") });
      index += 1;
    } else if (trimmed.startsWith(">")) {
      const quote: string[] = [];
      while (index < lines.length && lineAt(index).trim().startsWith(">")) {
        quote.push(lineAt(index).trim().replace(/^>\s?/, ""));
        index += 1;
      }
      blocks.push({ kind: "quote", text: quote.join(" ") });
    } else if (trimmed.startsWith("|")) {
      const tableLines: string[] = [];
      while (index < lines.length && lineAt(index).trim().startsWith("|")) {
        tableLines.push(lineAt(index));
        index += 1;
      }
      const rows = tableLines.filter((row) => !isSeparator(row)).map(cells);
      blocks.push({ kind: "table", header: rows[0] ?? [], rows: rows.slice(1) });
    } else if (LIST_ITEM.test(trimmed)) {
      const items: string[] = [];
      while (index < lines.length) {
        const current = lineAt(index);
        const currentTrimmed = current.trim();
        if (LIST_ITEM.test(currentTrimmed)) {
          items.push(currentTrimmed.slice(2));
        } else if (currentTrimmed !== "" && /^\s/.test(current) && items.length > 0) {
          // An indented line continues the item above (a wrapped list item).
          items[items.length - 1] = `${items[items.length - 1]} ${currentTrimmed}`;
        } else {
          break;
        }
        index += 1;
      }
      blocks.push({ kind: "list", items });
    } else {
      const paragraph: string[] = [];
      while (index < lines.length && !(paragraph.length > 0 && startsBlock(lineAt(index).trim()))) {
        paragraph.push(lineAt(index).trim());
        index += 1;
      }
      blocks.push({ kind: "paragraph", text: paragraph.join(" ") });
    }
  }
  return blocks;
}

/** Text with `code spans` as <code> elements; everything stays a text node. */
function inline(text: string): ReactNode[] {
  return text.split("`").map((part, index) =>
    index % 2 === 1 ? (
      <code key={index} className="rounded bg-paper-sunken px-1 font-mono text-[0.95em]">
        {part}
      </code>
    ) : (
      part
    ),
  );
}

export function PlainMarkdown({ text, className }: { text: string; className?: string }) {
  const blocks = parseBlocks(text);
  return (
    <div className={cn("flex flex-col gap-3 text-sm leading-relaxed [overflow-wrap:anywhere]", className)}>
      {blocks.map((block, index) => {
        switch (block.kind) {
          case "heading":
            return (
              <p key={index} className="font-semibold">
                {inline(block.text)}
              </p>
            );
          case "list":
            return (
              <ul key={index} className="list-disc space-y-1 pl-5">
                {block.items.map((item, itemIndex) => (
                  <li key={itemIndex}>{inline(item)}</li>
                ))}
              </ul>
            );
          case "quote":
            return (
              <blockquote key={index} className="border-l-2 border-rule-strong pl-3 text-muted-foreground">
                {inline(block.text)}
              </blockquote>
            );
          case "code":
            return (
              <pre key={index} className="whitespace-pre-wrap rounded-md bg-paper-sunken p-2.5 font-mono text-xs">
                {block.text}
              </pre>
            );
          case "table":
            return (
              // The table can scroll sideways on a phone, so the box must be reachable from the keyboard
              // (axe scrollable-region-focusable): a focusable, named region, as in chart-frame.tsx.
              <div
                key={index}
                role="region"
                aria-label={`Table ${index + 1}`}
                tabIndex={0}
                className="overflow-x-auto rounded-md border border-border outline-none focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring"
              >
                <table className="figure-nums w-full text-left text-xs [overflow-wrap:normal]">
                  <thead className="bg-paper-sunken">
                    <tr>
                      {block.header.map((cell, cellIndex) => (
                        <th key={cellIndex} scope="col" className="px-2.5 py-1.5 font-semibold">
                          {inline(cell)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, rowIndex) => (
                      <tr key={rowIndex} className="border-t border-border">
                        {row.map((cell, cellIndex) => (
                          <td key={cellIndex} className="px-2.5 py-1.5 align-top">
                            {inline(cell)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case "paragraph":
            return <p key={index}>{inline(block.text)}</p>;
        }
      })}
    </div>
  );
}
