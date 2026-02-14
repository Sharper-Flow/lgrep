import { tool } from "@opencode-ai/plugin"

export default tool({
  description:
    "Semantic code search using Voyage Code 3 embeddings. " +
    "Returns file paths, line ranges, and code snippets ranked by relevance. " +
    "Use natural language queries — understands code meaning, not just text patterns.",
  args: {
    q: tool.schema.string().describe("Natural language search query"),
    path: tool.schema
      .string()
      .describe("Absolute path to the project to search"),
    m: tool.schema
      .number()
      .default(10)
      .describe("Maximum number of results (default: 10)"),
  },
  async execute(args, context) {
    const projectPath = args.path || context.worktree || context.directory
    const result = await Bun.$`lgrep search ${args.q} ${projectPath} -m ${args.m}`.nothrow().text()
    return result.trim()
  },
})
