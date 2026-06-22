import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Renders Markdown the way the downloaded `.md` file will look.
 *
 * react-markdown does not render embedded raw HTML and sanitizes link
 * protocols by default, so scraped content from arbitrary sites is safe to
 * display here without an extra sanitizer. GitHub-flavoured tables/strikethrough
 * come from remark-gfm; visual styling lives in the `.markdown-body` CSS scope.
 */
export function MarkdownView({
  markdown,
  className = "",
}: {
  markdown: string;
  className?: string;
}) {
  return (
    <div className={`markdown-body ${className}`.trim()}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node, ...props }) => (
            <a {...props} target="_blank" rel="noopener noreferrer nofollow" />
          ),
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
